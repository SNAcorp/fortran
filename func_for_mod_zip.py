import logging
import zipfile
import shutil
import os
import subprocess
import re
from multiprocessing import Process, Queue
from fastapi import HTTPException
from models import File


def process_zip_file(file_id: int, zip_path: str, db):
    file = db.query(File).filter(File.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    file.status = "processing"
    db.commit()

    queue = Queue()
    process = Process(target=modify_fortran_files, args=(zip_path, queue))
    process.start()
    process.join()

    result = queue.get()
    if result['status'] == 'success':
        file.modified_filename = os.path.basename(result['modified_zip_path'])
        file.status = "ready"
    else:
        file.status = "error"
        logging.error(f"Error processing file {file_id}: {result['error']}")

    db.commit()


def modify_fortran_files(zip_path: str, queue: Queue):
    try:
        temp_dir = "temp"
        os.makedirs(temp_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        for root, _, files in os.walk(temp_dir):
            for file in files:
                if file.endswith(".for"):
                    file_path = os.path.join(root, file)
                    add_directives(file_path)

        compile_fortran_files(temp_dir)

        modified_zip_path = zip_path.replace(".zip", "_mod.zip")
        with zipfile.ZipFile(modified_zip_path, 'w') as zip_ref:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    zip_ref.write(file_path, os.path.relpath(file_path, temp_dir))

        shutil.rmtree(temp_dir)
        queue.put({'status': 'success', 'modified_zip_path': modified_zip_path})
    except Exception as e:
        queue.put({'status': 'error', 'error': str(e)})


def add_directives(file_path: str):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    new_lines = []
    implicit_none = False
    for line in lines:
        if "IMPLICIT NONE" in line.upper():
            implicit_none = True
            continue
        new_lines.append(line)

    if implicit_none:
        variables = extract_variables(new_lines)
        for var in variables:
            new_lines.insert(0, f"!$OMP THREADPRIVATE({var})\n")

    with open(file_path, 'w') as file:
        file.writelines(new_lines)


def extract_variables(lines: list) -> list:
    variables = []
    for line in lines:
        if "::" in line:
            parts = line.split("::")
            if len(parts) > 1:
                var_names = parts[1].split(',')
                variables.extend([var.strip() for var in var_names])
    return variables


def compile_fortran_files(directory: str):
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".for"):
                file_path = os.path.join(root, file)
                compile_file(file_path)


def compile_file(file_path: str):
    with open(file_path, 'r') as file:
        content = file.read()

    content = "IMPLICIT NONE\n" + content
    with open(file_path, 'w') as file:
        file.write(content)

    compile_command = ["gfortran", "-c", file_path]
    result = subprocess.run(compile_command, capture_output=True, text=True)

    if result.returncode != 0:
        errors = parse_errors(result.stderr)
        update_file_with_threadprivate(file_path, errors)


def parse_errors(stderr: str) -> list:
    errors = []
    for line in stderr.splitlines():
        match = re.search(r"'(\w+)' at .*", line)
        if match:
            errors.append(match.group(1))
    return errors


def update_file_with_threadprivate(file_path: str, variables: list):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    new_lines = []
    for line in lines:
        if "IMPLICIT NONE" in line.upper():
            continue
        new_lines.append(line)

    for var in variables:
        new_lines.insert(0, f"!$OMP THREADPRIVATE({var})\n")

    with open(file_path, 'w') as file:
        file.writelines(new_lines)
