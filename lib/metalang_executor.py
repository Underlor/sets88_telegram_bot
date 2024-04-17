from typing import Any
import operator
from re import VERBOSE

from funcparserlib.parser import a
from funcparserlib.parser import tok
from funcparserlib.parser import many
from funcparserlib.parser import skip
from funcparserlib.parser import finished
from funcparserlib.parser import forward_decl
from funcparserlib.parser import maybe
from funcparserlib.lexer import make_tokenizer
from funcparserlib.lexer import make_tokenizer, Token, TokenSpec


regexps = {
    "escaped": r"""
        \\                                  # Escape
          ((?P<standard>["\\/bfnrt])        # Standard escapes
        | (u(?P<unicode>[0-9A-Fa-f]{4})))   # uXXXX
        """,
    "unescaped": r"""
        [^"\\]                              # Unescaped: avoid ["\\]
        """,
}


class TaskExecutionError(Exception):
    pass


class MetalangParser:
    SPECS = [
        TokenSpec('elif', 'elif'),
        TokenSpec('if', 'if'),
        TokenSpec('else', 'else'),
        TokenSpec('space', r'[ \t\r\n]+'),
        TokenSpec("string", r'"(%(unescaped)s | %(escaped)s)*"' % regexps, VERBOSE),
        TokenSpec(
            "number",
            r"""
            -?                  # Minus
            (0|([1-9][0-9]*))   # Int
            (\.[0-9]+)?         # Frac
            ([Ee][+-]?[0-9]+)?   # Exp
            """,
            VERBOSE
        ),
        TokenSpec("name", r"[A-Za-z_][A-Za-z_0-9]*"),
        TokenSpec('op', r'[\+\-\*\,\(\)]',),
        TokenSpec('block', r'{|}',),
        TokenSpec('comp', r'<|>|>=|<=|==|!=',),
        TokenSpec('eq', r'=',),
    ]
    USEFUL = ['name', 'op', 'number', 'comp', 'eq', 'block', 'string', 'elif', 'if', 'else']

    def __init__(self):
        self.tokenizer = make_tokenizer(self.SPECS)
        self.parser = self.prepare_parser()

    def prepare_parser(self):
        def make_number(s: str):
            try:
                return int(s)
            except ValueError:
                return float(s)
        # Парсинг
        tokval = lambda x: x.value
        op = lambda s: a(Token('op', s)) >> tokval
        block = lambda s: a(Token('block', s)) >> tokval
        elif_op = tok('elif')
        if_op = tok('if')
        else_op = tok('else')
        comp_op = tok('comp')
        eq = tok('eq')
        name = tok('name')
        number = tok('number') >> make_number
        string = tok('string')

        ident = name

        comp_stmt = forward_decl()

        call_f_stmt = forward_decl()
        arg = comp_stmt | number | string | call_f_stmt | ident
        named_arg = ident + skip(eq) + (arg) >> (lambda x: {x[0]: x[1]})

        arg_list = (
            (named_arg | arg) + many(
                skip(op(',')) +
                    (named_arg | arg)
            )
        ) >> (lambda x: [x[0]] + x[1])

        call = (
            ident + skip(op('(')) + maybe(arg_list) + skip(op(')'))
        ) >> (lambda x: ('call', x[0], x[1] if len(x) > 1 else []))

        call_f_stmt.define(call)
        expr = comp_stmt | call | arg
        comp_stmt.define(
            (call | number | string | ident) + comp_op +
            (call | call | number | string | ident) >> (lambda x: ('comp', x[0], x[1], x[2]))
        )
        assign = ident + skip(eq) + expr >> (lambda x: ('assign', x[0], x[1]))
        ifblock = forward_decl()
        stmt = assign | ifblock | call

        ifstmt = (
            if_op + expr +
            skip(block('{')) + many(stmt) + skip(block('}')) +
            maybe(many(elif_op + expr + skip(block('{')) + many(stmt) + skip(block('}')))) +
            maybe(else_op + skip(block('{')) + many(stmt) + skip(block('}')))
        )

        ifblock.define(ifstmt)
        parser = many(stmt) + skip(finished)
        return parser

    def parse(self, text: str) -> list:
        tokens = [t for t in self.tokenizer(text) if t.type in self.USEFUL]
        return self.parser.parse(tokens)


class Debugger:
    def __init__(self) -> None:
        self.messages = []

    def log(self, message) -> None:
        self.messages.append(message)


class MetaLangExecutor:
    COMMANDS = []

    def __init__(self) -> None:
        self.vars = {}
        self.debug: Debugger|None = None

    async def parse(self, code: str) -> None:
        parser = MetalangParser()
        return parser.parse(code)

    def debug_log(self, message) -> None:
        if self.debug:
            self.debug.log(message)

    async def process_assign_command(self, task: tuple) -> None:
        key = task[1]
        val = await self.process_entity(task[2])
        
        self.vars[key] = val

        self.debug_log(f'Processing assignment "{key}" = "{val}"')

    async def process_call_command(self, task: tuple) -> Any:
        function_name = task[1]
        if function_name not in self.COMMANDS:
            raise TaskExecutionError(f'Invalid call function "{function_name}"')

        args = []
        kwargs = {}
        for argument in task[2]:
            if isinstance(argument, (dict)):
                for key, val in argument.items():
                    kwargs[key] = await self.process_entity(val)
                continue
            args.append(await self.process_entity(argument))

        self.debug_log(f'Processing call "{function_name}" with args "{args}" and kwargs "{kwargs}"')

        return await getattr(self, function_name)(*args, **kwargs)

    async def process_command(self, task: tuple) -> Any:
        if task[0] == 'assign':
            return await self.process_assign_command(task)

        elif task[0] == 'call':
            return await self.process_call_command(task)

        elif task[0] == 'comp':
            if len(task) == 2:
                self.debug_log(f'Processing condition of "{task[1]}"')

                return task[1]

            op_str = task[2]
            op = None
            if op_str == '>':
                op = operator.gt
            elif op_str == '>=':
                op = operator.ge
            elif op_str == '<':
                op = operator.lt
            elif op_str == '<=':
                op = operator.le
            elif op_str == '==':
                op = operator.eq
            elif op_str == '!=':
                op = operator.ne
            else:
                raise TaskExecutionError(f'Invalid comparison operator "{op_str}"')
            entity1 = await self.process_entity(task[1])
            entiry2 = await self.process_entity(task[3])

            self.debug_log(f'Processing comparison "{entity1}" {op_str} "{entiry2}"')

            return op(entity1, await self.process_entity(task[3]))
        elif task[0] == 'if':
            cond = task[1]
            if isinstance(cond, (int, float, str)):
                cond = ('comp', cond)

            if await self.process_command(cond):
                self.debug_log('If with condition is true')

                await self.process_tree(task[2])
                return

            for elif_statement in task[3]:
                elif_cond = elif_statement[1]
                if isinstance(cond, (int, float, str)):
                    elif_cond = ('comp', elif_cond)

                if await self.process_command(cond):
                    self.debug_log('Elif with condition matched')
                    await self.process_tree(elif_statement[2])
                    return
            if task[4]:
                self.debug_log('Else condition matched')
                await self.process_tree(task[4][1])
                return

            return

        raise TaskExecutionError(f'Invalid command "{task[0]}"')

    async def process_entity(self, entity: tuple) -> Any:
        if isinstance(entity, (int, float, bool)):
            return entity
        elif isinstance(entity, tuple):
            return await self.process_command(entity)
        elif isinstance(entity, str):
            if entity.startswith('"'):
                return entity[1:-1]
            return self.vars.get(entity)
        raise TaskExecutionError(f'Invalid entity {type(entity)}')

    async def process_tree(self, tasks: list[dict]) -> None:
        self.debug_log(f'Processing tree with {len(tasks)} tasks')

        if isinstance(tasks, (tuple, list)):
            for command in tasks:
                await self.process_entity(command)

    async def run(
        self,
        code: str|None = None,
        parsed_code: list[dict]|None = None,
        debug: bool = False
    ) -> None:
        if debug:
            self.debug = Debugger()
        else:
            self.debug = None

        if not code and not parsed_code:
            raise TaskExecutionError('No code to run')
        if code:
            parsed_code = self.parse(code)

        await self.process_tree(parsed_code)

        if self.debug:
            return self.debug.messages
