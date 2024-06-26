from fastapi import FastAPI, Depends, HTTPException, Request, status, Form, File, UploadFile
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from database import get_db, Base, engine
from models import User, File as FileModel
from jwt_utils import create_access_token, get_current_user
import shutil
import os
from function_for_mod import modificate
from datetime import timedelta, datetime
import uuid
import uvicorn
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup code
    Base.metadata.create_all(bind=engine)
    yield
    # Shutdown code (if any)


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS_TOKEN_EXPIRE_MINUTES = 30


class UserCreate(BaseModel):
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserInDB(BaseModel):
    email: EmailStr

    class Config:
        from_attributes = True


def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email).first()


def create_user(db: Session, user: UserCreate):
    hashed_password = pwd_context.hash(user.password)
    db_user = User(email=user.email, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


@app.middleware("http")
async def add_token_to_header(request: Request, call_next):
    token = request.cookies.get("access_token")
    if token:
        request.headers.__dict__["_list"].append(
            (b"authorization", f"Bearer {token}".encode())
        )
    response = await call_next(request)
    return response


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
        raise HTTPException(status_code=400, detail="Invalid credentials")
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": db_user.email}, expires_delta=access_token_expires
    )
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="access_token", value=access_token, httponly=True)
    return response


@app.get("/logout", response_class=HTMLResponse)
def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
              search: str = '', sort_by: str = 'date'):
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
    return templates.TemplateResponse("dashboard.html", {"request": request, "files": files, "user": current_user})


@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("upload.html", {"request": request, "user": current_user})


@app.post("/upload")
def upload_file(
        title: str = Form(...),
        description: str = Form(...),
        hashtags: str = Form(...),
        file: UploadFile = File(...),
        is_public: bool = Form(False),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if not file.filename.endswith(('.f', '.for', '.f90', '.f95', '.f03', '.f08')):
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload a Fortran file.")

    contents = file.file.read()
    original_filename = file.filename
    file_id = str(uuid.uuid4())
    original_path = f"uploads/{file_id}_{original_filename}"
    modified_path = f"uploads/{file_id}_modified_{original_filename}"

    with open(original_path, 'wb+') as out_file:
        out_file.write(contents)

    modificate(original_path, modified_path)

    db_file = FileModel(
        original_filename=original_path,
        modified_filename=modified_path,
        title=title,
        description=description,
        owner_id=current_user.id,
        is_public=is_public,
        hashtags=hashtags
    )
    db.add(db_file)
    db.commit()
    db.refresh(db_file)

    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    users = db.query(User).all()
    files = db.query(FileModel).all()
    return templates.TemplateResponse("admin.html", {"request": request, "users": users, "files": files})


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


@app.exception_handler(404)
def not_found(request: Request, exc):
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)


@app.exception_handler(500)
def internal_server_error(request: Request, exc):
    return templates.TemplateResponse("500.html", {"request": request}, status_code=500)



if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
