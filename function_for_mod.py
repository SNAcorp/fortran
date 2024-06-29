from pyparsing import Word, alphas, alphanums, Group, ZeroOrMore, Optional, Suppress, Keyword, Combine, nums, \
    restOfLine, OneOrMore, ParseResults
from fastapi import HTTPException
import re

from models import File

keywords = {'INTEGER', 'REAL', 'DOUBLE', 'CHARACTER',
            'LOGICAL', 'COMPLEX', 'COMMON', 'DATA',
            'IMPLICIT', "ACCESS", "ASSIGN", "BACKSPACE",
            "BLOCK DATA", "CALL", "CLOSE", "COMMON",
            "CONTINUE", "DATA", "DIMENSION", "DO", "ELSE",
            "ELSE IF", "END", "ENDIF", "ENDFILE",
            "ENTRY", "EQUIVALENCE", "EXTERNAL", "FORMAT",
            "FUNCTION", "GOTO", "IF", "IMPLICIT",
            "INQUIRE", "INTRINSIC", "LOGICAL", "OPEN",
            "PARAMETER", "PAUSE", "PRINT", "PROGRAM",
            "READ", "RETURN", "REWIND", "SAVE", "STOP",
            "SUBROUTINE", "THEN", "WRITE", "ALLOCATABLE",
            "ALLOCATE", "CASE", "CYCLE", "DEALLOCATE",
            "END DO", "END IF", "EXIT", "INTERFACE",
            "INTENT", "FROM", "NOT", "AND", "OR",
            "MODULE", "NAMELIST", "NULLIFY", "OPTIONAL",
            "POINTER", "PRIVATE", "PROCEDURE", "PUBLIC",
            "RECURSIVE", "RESULT", "SELECT CASE", "TARGET",
            "USE", "WHERE", "ELEMENTAL", "FORALL",
            "PURE", "ABSTRACT", "ASSOCIATE", "BIND", "ENUM",
            "ENUMERATOR", "EXTENDS", "FINAL",
            "GENERIC", "IMPORT", "NON_INTRINSIC",
            "NON_OVERRIDABLE", "PASS", "PROTECTED", "VALUE",
            "VOLATILE", "BLOCK", "CODATA", "CONTAINS",
            "CRITICAL", "END BLOCK", "END CRITICAL",
            "END SUBMODULE", "ERROR STOP", "SUBMODULE",
            "IMPURE", "NON_RECURSIVE"}

# Define Fortran variable declaration
integer = Keyword("INTEGER")
real = Keyword("REAL")
double = Keyword("DOUBLE PRECISION")
character = Keyword("CHARACTER")
logical = Keyword("LOGICAL")
complex_type = Keyword("COMPLEX")

identifier = Word(alphas, alphanums + '_')
char_length = Combine(Suppress("*") + Word(nums))
char_decl = character + Optional(char_length)("length") + Group(identifier + ZeroOrMore(Suppress(",") + identifier))

array_spec = Suppress('(') + Group(
    ZeroOrMore(Word(nums + alphas + '*,:') + Suppress(',')) + Word(nums + alphas + '*,:')) + Suppress(')')
declaration = (integer | real | double | char_decl | logical | (complex_type + Optional(char_length))) + Group(
    identifier + Optional(array_spec) + ZeroOrMore(Suppress(",") + identifier + Optional(array_spec)))

common_block = Suppress("COMMON") + Group(ZeroOrMore(Suppress("/") + identifier + Suppress("/") + Group(
    ZeroOrMore(identifier + Optional(array_spec) + Suppress(",") | identifier + Suppress(",")) + identifier + Optional(
        array_spec))))

data_block = Suppress("DATA") + Group(OneOrMore(
    identifier + Optional(array_spec) + Suppress("/") + Optional(Word(alphanums + '.')) + ZeroOrMore(
        Suppress(",") + identifier + Optional(array_spec) + Suppress("/") + Optional(Word(alphanums + '.')))))

implicit = Keyword("IMPLICIT") + restOfLine

# Rules for implicit variables
implicit_rules = {
    'INTEGER': set('IJKLMNOPQRSTUVWXYZ'),
    'REAL': set('A-H')
}


def remove_comments_and_convert_multiline_declarations(input_file, output_file):
    with open(input_file, 'r') as file:
        lines = file.readlines()

    result_lines = []
    current_declaration = []

    for line in lines:
        stripped_line = line.strip()

        # Игнорируем строки, начинающиеся с 'c', 'C', '!'
        if stripped_line.startswith(('c', 'C', '!')):
            continue

        if '!' in stripped_line:
            stripped_line = stripped_line.split('!')[0]


        if len(stripped_line) > 0:
            if stripped_line[1:].strip().startswith(("\'", "\'")):
                continue

            # if '=' in stripped_line:
            #     stripped_line = stripped_line.split('=')[0]
            #     c = 1
            #     result = ""
            #     while True:
            #         result += stripped_line[-1 * c]
            #         if stripped_line[-1 * c + 1] == ' ':
            #             stripped_line = result[::-1]
            #             break
            # else:
            #     continue

            if stripped_line[0].isdigit():
                continue


        # Проверяем, является ли строка продолжением объявления или содержит объявления
        if stripped_line.startswith('&'):
            current_declaration.append(stripped_line[1:].strip())
        else:
            if current_declaration:
                combined_line = ' '.join(current_declaration).replace(' ,', ',').replace('  ', ' ')
                result_lines.append(combined_line)
                current_declaration = []
            if stripped_line.endswith(','):
                current_declaration.append(stripped_line)
            else:
                result_lines.append(stripped_line)

    # Если осталась незавершенная строка объявления
    if current_declaration:
        combined_line = ' '.join(current_declaration).replace(' ,', ',').replace('  ', ' ')
        result_lines.append(combined_line)

    return '\n'.join(result_lines) + '\n'


# Function to determine the type of implicit variable
def get_implicit_type(var, implicit_none):
    if implicit_none:
        raise ValueError(f"Implicit declaration not allowed for variable: {var}")
    first_char = var[0]
    for var_type, letters in implicit_rules.items():
        if first_char in letters:
            return var_type
    return 'REAL'  # Default implicit type if no rules match


def check_variable(variable):
    try:
        float(variable)
    except:
        return False
    return True


# Parse function and subroutine blocks
def parse_fortran_code(code):
    lines = code.splitlines()
    variables = set()
    declared_vars = set()
    implicit_none = False

    for line in lines:
        line = line.strip().upper()
        if line.startswith(('INTEGER', 'REAL', 'DOUBLE PRECISION', 'CHARACTER', 'LOGICAL', 'COMPLEX')):
            try:
                parsed_line = declaration.parseString(line)
                for item in parsed_line[1:]:
                    if isinstance(item, ParseResults):
                        declared_vars.update(item)
                        variables.update(item)
                    else:
                        declared_vars.add(item)
                        variables.add(item)
            except Exception as e:
                print(f"Error parsing line: {line}\n{e}")
        elif line.startswith('COMMON'):
            try:
                parsed_line = common_block.parseString(line)
                for item in parsed_line[0]:
                    if isinstance(item, ParseResults):
                        declared_vars.update(item)
                        variables.update(item)
                    else:
                        declared_vars.add('/' + item + '/')
                        variables.add('/' + item + '/')
            except Exception as e:
                print(f"Error parsing line: {line}\n{e}")
        elif line.startswith('DATA'):
            try:
                parsed_line = data_block.parseString(line)
                for item in parsed_line[0]:
                    if isinstance(item, ParseResults):
                        declared_vars.update(item)
                        variables.update(item)
                    else:
                        declared_vars.add('/' + item + '/')
                        variables.add('/' + item + '/')
            except Exception as e:
                print(f"Error parsing line: {line}\n{e}")
        elif line.startswith('IMPLICIT NONE'):
            implicit_none = True
        elif line.startswith('IMPLICIT'):
            # Handle implicit declarations here if necessary
            pass

    # Identify implicit variables
    for line in lines:
        words = line.split()
        if words and words[0] not in ('INTEGER', 'REAL', 'DOUBLE PRECISION', 'CHARACTER', 'LOGICAL', 'COMPLEX'):
            for word in words:
                # print("word " + word)
                clean_word = word.strip(',').split('(')[0].split('!')[0]  # Remove potential array dimensions
                clean_word = clean_word.split('=')[0]
                # print("clean_word " + word)
                if clean_word.upper() in keywords or clean_word.isdigit():
                    continue
                if clean_word in declared_vars or not clean_word.isalpha() or check_variable(word):
                    continue
                try:
                    var_type = get_implicit_type(clean_word, implicit_none)
                    print(f"Implicit variable found: {clean_word}, Type: {var_type}")
                    variables.add(clean_word)
                except ValueError as ve:
                    print(ve)

    return variables


# Add THREADPRIVATE directive
def add_threadprivate_directive(code, variables):
    modified_lines = []
    for var in variables:
        if isinstance(var, str):
            modified_lines.append(f"!$OMP THREADPRIVATE({var})")
    print("count of mod lines: " + str(len(modified_lines)))
    return  "\n".join(modified_lines) + "\n"


def modificate(file_id, input_file, output_file, db):
    filer = db.query(File).filter(File.id == file_id).first()
    if not filer:
        raise HTTPException(status_code=404, detail="File not found")

    with open(input_file, 'r') as file:
        code = file.read()
    program = code
    filer.status = "processing"
    db.commit()
    variables = parse_fortran_code(remove_comments_and_convert_multiline_declarations(input_file, output_file))
    print("Variables found:", variables)

    result = add_threadprivate_directive(code, variables)

    with open(output_file, 'w') as file:
        file.write(result + program)

    filer.status = "ready"
    db.commit()

# 435
