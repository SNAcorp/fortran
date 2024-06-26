import asyncio
import json
import threading
from typing import List
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Depends, HTTPException, Request, status, Form, File, UploadFile, BackgroundTasks, \
    WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, ValidationError
from database import get_db, Base, engine, SessionLocal
from func_for_mod_zip import process_zip_file
from models import User, File as FileModel
from models import ModifierVersion
from jwt_utils import create_access_token, get_current_user
import shutil
import os
from function_for_mod import modificate
from datetime import timedelta, datetime
import uuid
import uvicorn
from contextlib import asynccontextmanager
import jwt
import redis

redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = os.getenv("REDIS_PORT", 6379)
redis_client = redis.StrictRedis(host=redis_host, port=redis_port, db=0)
REDIS_KEY = "public_files"
REDIS_TTL = 600  # Время жизни кэша в секундах

# redis_client = redis.StrictRedis(host='localhost', port=6379, db=0)
# REDIS_KEY = "public_files"
# REDIS_TTL = 600  # Время жизни кэша в секундах


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup code
    Base.metadata.create_all(bind=engine)
    yield
    # Shutdown code (if any)


MODIFIER_VERSION_FILE = "modifier_version.txt"
app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS_TOKEN_EXPIRE_MINUTES = 120

BACKUPS = "backups"
MODIFIER_DIR = "modifiers"
UPLOADS = "uploads"
if not os.path.exists(MODIFIER_DIR):
    os.makedirs(MODIFIER_DIR)

if not os.path.exists(BACKUPS):
    os.makedirs(BACKUPS)

if not os.path.exists(UPLOADS):
    os.makedirs(UPLOADS)

SECRET_KEY = "IloveSNA1942"
ALGORITHM = "HS256"

class WebSocketManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = WebSocketManager()

def check_file_processing_status(db: Session, file_id: int):
    file = db.query(FileModel).filter(FileModel.id == file_id).first()
    if file:
        return file.status
    return None

async def update_file_status():
    while True:
        db = SessionLocal()
        files = db.query(FileModel).filter(FileModel.status == "processing").all()
        for file in files:
            # Проверка состояния обработки файла
            new_status = check_file_processing_status(db, file.id)
            if file.status != new_status:
                file.status = new_status
                db.commit()
                await manager.broadcast(json.dumps({"file_id": file.id, "status": file.status}))
        db.close()
        await asyncio.sleep(5)

def start_background_task():
    loop = asyncio.get_event_loop()
    loop.create_task(update_file_status())

from fastapi import WebSocket, WebSocketDisconnect

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


class UserCreate(BaseModel):
    email: EmailStr
    password: str


class FileResponse(BaseModel):
    id: int
    title: str
    description: str
    upload_date: str
    owner_email: str
    download_count: int
    status: str
    original_url: str
    modified_url: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserInDB(BaseModel):
    email: EmailStr

    class Config:
        from_attributes = True


def get_latest_modifier_version(db: Session):
    return len(db.query(ModifierVersion).order_by(ModifierVersion.id).all())


def get_public_files_from_cache():
    cached_data = redis_client.get(REDIS_KEY)
    if cached_data:
        return json.loads(cached_data)
    return None


def set_public_files_to_cache(data):
    redis_client.set(REDIS_KEY, json.dumps(data), ex=REDIS_TTL)


def clear_public_files_cache():
    redis_client.delete(REDIS_KEY)


def check_file_processing_status(db: Session, file_id: int):
    file = db.query(FileModel).filter(FileModel.id == file_id).first()
    if file:
        return file.status
    return None


def create_user(db: Session, user: UserCreate):
    hashed_password = pwd_context.hash(user.password)
    is_admin = user.email == "stepanov.iop@gmail.com"
    db_user = User(email=user.email, hashed_password=hashed_password, is_admin=is_admin, is_active=1 if is_admin else 0)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user





def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email).first()


def get_modifier_version():
    if os.path.exists(MODIFIER_VERSION_FILE):
        with open(MODIFIER_VERSION_FILE, 'r') as f:
            return f.read().strip()
    return "Unknown"


def set_modifier_version(db: Session, file_path: str):
    modifier_version = ModifierVersion(file_path=file_path)
    db.add(modifier_version)
    db.commit()
    return modifier_version


@app.middleware("http")
async def add_token_to_header(request: Request, call_next):
    token = request.cookies.get("access_token")
    if token:
        request.headers.__dict__["_list"].append(
            (b"authorization", f"Bearer {token}".encode())
        )
    response = await call_next(request)
    if response.status_code == 401:
        response = RedirectResponse(url="/login?expired=true")
        response.delete_cookie("access_token")
    return response


@app.post("/remodify/{file_id}")
def remodify_file(file_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
                  request: Request = None):
    file = db.query(FileModel).filter(FileModel.id == file_id, FileModel.owner_id == current_user.id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    latest_modifier_version = get_latest_modifier_version(db)
    if not latest_modifier_version:
        raise HTTPException(status_code=500, detail="No modifier version found")

    if file.modifier_version_id == latest_modifier_version:
        return JSONResponse(status_code=400,
                            content={"message": "File is already modified with the current version of the modifier"})

    try:
        modificate(file.id, file.original_filename, file.modified_filename, db)
        file.modifier_version_id = latest_modifier_version
        db.commit()
        return JSONResponse(status_code=200,
                            content={"message": "File successfully remodified with the latest version of the modifier"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"message": f"An error occurred: {str(e)}"})



@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    user = None
    if token is not None:
        user = get_current_user(db, token)
    if user is not None:
        return templates.TemplateResponse("index.html", {"request": request, "user": user})
    else:
        return templates.TemplateResponse("index.html", {"request": request})


@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("profile.html", {"request": request, "user": current_user})


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
def register(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db), request: Request = None):
    user = UserCreate(email=email, password=password)
    db_user = get_user_by_email(db, user.email)
    if db_user:
        if request:
            request.state.error = "Email already registered"
        return templates.TemplateResponse("register.html", {"request": request})
    create_user(db, user)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    db_user = get_user_by_email(db, email)
    if not db_user or not pwd_context.verify(password, db_user.hashed_password):
        return RedirectResponse(url="/login?error=true", status_code=status.HTTP_302_FOUND)
    if db_user.is_active == 2:
        raise HTTPException(status_code=403, detail="User access denied")
    if db_user.is_active == 0:
        return RedirectResponse(url="/awaiting-approval", status_code=status.HTTP_302_FOUND)
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": db_user.email}, expires_delta=access_token_expires
    )
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="access_token", value=access_token, httponly=True)
    return response


@app.get("/logout", response_class=HTMLResponse)
def logout(request: Request, current_user: User = Depends(get_current_user)):
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
              search: str = '', sort_by: str = 'date'):
    if current_user.is_active == 0:
        return RedirectResponse(url="/awaiting-approval", status_code=status.HTTP_302_FOUND)
    if current_user.is_active == 2:
        return RedirectResponse(url="/access-denied", status_code=status.HTTP_302_FOUND)

    query = db.query(FileModel).filter((FileModel.owner_id == current_user.id) | (FileModel.is_public == True))

    if search:
        query = query.filter((FileModel.title.contains(search)) | (FileModel.description.contains(search)) | (
            FileModel.hashtags.contains(search)))

    if sort_by == 'date':
        query = query.order_by(FileModel.upload_date.desc())
    elif sort_by == 'downloads':
        query = query.order_by(FileModel.download_count.desc())
    elif sort_by == 'title':
        query = query.order_by(FileModel.title)

    files = query.all()
    latest_modifier_version = get_latest_modifier_version(db)
    return templates.TemplateResponse("dashboard.html", {"request": request, "files": files, "user": current_user,
                                                         "modifier_version": latest_modifier_version})


@app.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket, db: Session = Depends(get_db)):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise ValidationError("Invalid token")
        current_user = db.query(User).filter(User.id == user_id).first()
        if current_user is None:
            raise ValidationError("User not found")
    except (jwt.PyJWTError, ValidationError):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    try:
        while True:
            files = db.query(FileModel).filter(
                (FileModel.owner_id == current_user.id) | (FileModel.is_public == True)).all()
            files_data = [{"id": file.id, "status": file.status} for file in files]
            await websocket.send_json(files_data)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        print("Client disconnected")


@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, current_user: User = Depends(get_current_user)):
    if current_user.is_active == 0:
        return RedirectResponse(url="/awaiting-approval", status_code=status.HTTP_302_FOUND)
    if current_user.is_active == 2:
        return RedirectResponse(url="/access-denied", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("upload.html", {"request": request, "user": current_user})


@app.post("/upload")
def upload_file(
        title: str = Form(...),
        description: str = Form(...),
        hashtags: str = Form(...),
        file_type: str = Form(...),
        file: UploadFile = File(...),
        is_public: bool = Form(False),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        request: Request = None,
        background_tasks: BackgroundTasks = None
):
    valid_fortran_extensions = ('.f', '.for', '.f90', '.f95', '.f03', '.f08')

    if file_type == "fortran" and not file.filename.endswith(valid_fortran_extensions):
        if request:
            request.state.error = "Invalid file type. Please upload a Fortran file."
        return templates.TemplateResponse("upload.html", {"request": request})

    if file_type == "zip" and not file.filename.endswith('.zip'):
        if request:
            request.state.error = "Invalid file type. Please upload a ZIP file."
        return templates.TemplateResponse("upload.html", {"request": request})

    if is_public:
        existing_file = db.query(FileModel).filter(FileModel.title == title, FileModel.is_public == True).first()
        if existing_file:
            if request:
                request.state.error = "A public file with this title already exists."
            return templates.TemplateResponse("upload.html", {"request": request})
    else:
        existing_file = db.query(FileModel).filter(FileModel.title == title,
                                                   FileModel.owner_id == current_user.id).first()
        if existing_file:
            if request:
                request.state.error = "You already have a file with this title in your list."
            return templates.TemplateResponse("upload.html", {"request": request})

    contents = file.file.read()
    original_filename = file.filename
    file_id = str(uuid.uuid4())
    original_path = f"uploads/{file_id}_{original_filename}"
    modified_path = f"uploads/{file_id}_modified_{original_filename}"

    with open(original_path, 'wb') as out_file:
        out_file.write(contents)

    latest_modifier_version = get_latest_modifier_version(db)
    if not latest_modifier_version:
        raise HTTPException(status_code=500, detail="No modifier version found")

    db_file = FileModel(
        original_filename=original_path,
        modified_filename=modified_path,
        title=title,
        description=description,
        owner_id=current_user.id,
        is_public=is_public,
        hashtags=hashtags,
        modifier_version_id=latest_modifier_version,
        status="waiting"
    )
    db.add(db_file)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if request:
            request.state.error = "A public file with this title already exists."
        return templates.TemplateResponse("upload.html", {"request": request})

    db.refresh(db_file)

    if file_type == "fortran":
        background_tasks.add_task(modificate, db_file.id, original_path, modified_path, db)
    else:
        background_tasks.add_task(process_zip_file, db_file.id, original_path, db)

    clear_public_files_cache()

    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        return RedirectResponse(url="/access-denied", status_code=status.HTTP_302_FOUND)

    # Получение списка пользователей, запросов на регистрацию и статистики
    users = db.query(User).all()
    pending_users = db.query(User).filter(User.is_active == 0).all()
    files = db.query(FileModel).all()
    total_files = len(files)
    total_users = len(users)

    # Получение списка версий модификаторов
    backups = db.query(ModifierVersion).order_by(ModifierVersion.upload_date.desc()).all()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "users": users,
        "pending_users": pending_users,
        "total_files": total_files,
        "total_users": total_users,
        "backups": backups,
        "current_user": current_user,
        "user": current_user
    })


@app.get("/access-denied", response_class=HTMLResponse)
def access_denied(request: Request):
    return templates.TemplateResponse("access_denied.html", {"request": request})


@app.get("/awaiting-approval", response_class=HTMLResponse)
def awaiting_approval(request: Request):
    return templates.TemplateResponse("awaiting_approval.html", {"request": request})


@app.get("/unauthorized", response_class=HTMLResponse)
def unauthorized(request: Request):
    return templates.TemplateResponse("unauthorized.html", {"request": request})


@app.get("/public-files", response_class=HTMLResponse)
def public_files(request: Request, db: Session = Depends(get_db), search: str = '', sort_by: str = 'date'):
    query = db.query(FileModel).filter(FileModel.is_public == True)
    token = request.cookies.get("access_token")
    user = None
    if token is not None:
        user = get_current_user(db, token)
    if search:
        query = query.filter((FileModel.title.contains(search)) | (FileModel.description.contains(search)) | (
            FileModel.hashtags.contains(search)))

    if sort_by == 'date':
        query = query.order_by(FileModel.upload_date.desc())
    elif sort_by == 'downloads':
        query = query.order_by(FileModel.download_count.desc())
    elif sort_by == 'title':
        query = query.order_by(FileModel.title)

    files = query.all()

    if user is not None:
        return templates.TemplateResponse("public_files.html", {"request": request, "files": files, "user": user})
    else:
        return templates.TemplateResponse("public_files.html", {"request": request, "files": files})


@app.get("/download/original/{file_id}", response_class=FileResponse)
def download_original(file_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    file = db.query(FileModel).filter(FileModel.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    if file.is_public or file.owner_id == current_user.id:
        return FileResponse(file.original_filename, filename=file.original_filename)
    raise HTTPException(status_code=403, detail="Not authorized to download this file")


@app.get("/download/modified/{file_id}", response_class=FileResponse)
def download_modified(file_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    file = db.query(FileModel).filter(FileModel.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    if file.is_public or file.owner_id == current_user.id:
        return FileResponse(file.modified_filename, filename=file.modified_filename)
    raise HTTPException(status_code=403, detail="Not authorized to download this file")


@app.post("/change-password")
def change_password(
        old_password: str = Form(...),
        new_password: str = Form(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if not pwd_context.verify(old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Old password is incorrect")
    current_user.hashed_password = pwd_context.hash(new_password)
    db.commit()
    return RedirectResponse(url="/profile", status_code=status.HTTP_302_FOUND)


@app.post("/admin/approve/{user_id}")
def approve_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = 1
        db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/reject/{user_id}")
def reject_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = 2
        db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/block/{user_id}")
def block_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = 2
        db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/unblock/{user_id}")
def unblock_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = 1
        db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/make-admin/{user_id}")
def make_admin(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_admin = True
        db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/upload-script")
async def upload_script(file: UploadFile = File(...), db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        return JSONResponse(status_code=403, content={"message": "Not authorized"})

    if not file.filename.endswith('.py'):
        return JSONResponse(status_code=400,
                            content={"message": "Invalid file type. Please upload a Python (.py) file."})

    temp_file_path = os.path.join(MODIFIER_DIR, f"temp_{uuid.uuid4()}.py")
    with open(temp_file_path, 'wb') as out_file:
        shutil.copyfileobj(file.file, out_file)

    return JSONResponse(status_code=200,
                        content={"message": "File uploaded successfully", "temp_file_path": temp_file_path})


@app.post("/admin/confirm-upload-script")
def confirm_upload_script(temp_file_path: str = Form(...), db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    current_script_path = os.path.join(MODIFIER_DIR, "function_for_mod.py")
    backup_script_path = os.path.join(MODIFIER_DIR, f"{datetime.utcnow().isoformat()}_{uuid.uuid4()}.py")

    if os.path.exists(current_script_path):
        shutil.copy(current_script_path, backup_script_path)
        set_modifier_version(db, backup_script_path)

    shutil.move(temp_file_path, current_script_path)
    set_modifier_version(db, current_script_path)

    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/restore-script/{version_id}")
def restore_script(version_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        return JSONResponse(status_code=403, content={"message": "Not authorized"})

    modifier_version = db.query(ModifierVersion).filter(ModifierVersion.id == version_id).first()
    if not modifier_version:
        return JSONResponse(status_code=404, content={"message": "Backup file not found."})

    current_script_path = os.path.join(MODIFIER_DIR, "function_for_mod.py")

    if os.path.exists(modifier_version.file_path):
        shutil.copy(modifier_version.file_path, current_script_path)
        set_modifier_version(db, modifier_version.file_path)
    else:
        return JSONResponse(status_code=404, content={"message": "Backup file not found."})

    return JSONResponse(status_code=200, content={"message": "Backup restored successfully"})


@app.get("/api/public-files", response_model=List[FileResponse])
def get_public_files(db: Session = Depends(get_db)):
    cached_files = get_public_files_from_cache()
    if cached_files:
        return cached_files

    files = db.query(FileModel).filter(FileModel.is_public == True).all()
    public_files = [
        {
            "id": file.id,
            "title": file.title,
            "description": file.description,
            "upload_date": file.upload_date.strftime("%Y-%m-%d"),
            "owner_email": file.owner.email,
            "download_count": file.download_count,
            "status": file.status,
            "original_url": f"/download/original/{file.id}",
            "modified_url": f"/download/modified/{file.id}" if file.status == "ready" else None
        }
        for file in files
    ]

    set_public_files_to_cache(public_files)
    return public_files


@app.exception_handler(404)
def not_found(request: Request, exc):
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)


@app.exception_handler(500)
def internal_server_error(request: Request, exc):
    return templates.TemplateResponse("500.html", {"request": request}, status_code=500)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=80, reload=True)
    # background_thread = threading.Thread(target=start_background_task)
    # background_thread.start()
