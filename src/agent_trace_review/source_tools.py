"""Bounded Python AST hints; never imports or executes inspected source."""

import ast
import re

from .util import redact


def _name(node):
    if isinstance(node, ast.Call):
        return _name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def python_tools(content, path):
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError, RecursionError):
        return []
    result = []
    for node in ast.walk(tree):
        function, name = None, None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(_name(d) == "tool" for d in node.decorator_list):
                function, name = node, node.name
        elif isinstance(node, ast.ClassDef) and any(_name(b) in {"Tool", "BaseTool"} for b in node.bases):
            for member in node.body:
                if isinstance(member, ast.Assign) and any(_name(t) == "name" for t in member.targets):
                    if isinstance(member.value, ast.Constant) and isinstance(member.value.value, str):
                        name = member.value.value
                if isinstance(member, ast.AnnAssign) and _name(member.target) == "name":
                    if isinstance(member.value, ast.Constant) and isinstance(member.value.value, str):
                        name = member.value.value
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name in {"forward", "_run"}:
                    function = member
        if name and function and redact(name) == name and re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,99}", name):
            result.append({
                "name": name, "path": path, "line": node.lineno,
                "parameters": [redact(a.arg) for a in [*function.args.posonlyargs, *function.args.args,
                                               *function.args.kwonlyargs] if a.arg not in {"self", "cls"}],
                "status": "static_candidate",
            })
        if len(result) >= 100:
            break
    return result
