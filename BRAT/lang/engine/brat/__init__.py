"""BRAT language package: lexer, parser, runtime."""
__version__ = "0.1"

from .lexer import Cur, lex
from .runtime import Done, run
from .parser import parse_prog