#!/usr/bin/env python3
"""Motor de diff textual do Codis.

Implementa ``DiffEngine``, que compara duas versões de um arquivo e produz
uma lista de ``DiffHunk`` - blocos de mudanças com contexto - usados pelo
``DiffPane`` para renderização visual lado a lado.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass

logger = logging.getLogger("Codis")


@dataclass
class DiffHunk:
    """
    Representa um **hunk** de diff, ou seja, um bloco contínuo de linhas com mudanças.

    Os números de linha são **1-based** (a primeira linha é 1), seguindo a convenção
    dos formatos de diff tradicionais (unified, context).

    Attributes:
        a_start: Número da primeira linha no arquivo original (1-based).
        a_count: Quantidade de linhas do arquivo original abrangidas pelo hunk.
        b_start: Número da primeira linha no arquivo modificado (1-based).
        b_count: Quantidade de linhas do arquivo modificado abrangidas pelo hunk.
        lines: Lista de tuplas (tipo, texto) representando as linhas do hunk.
               O tipo pode ser:
               - ' ' : linha de contexto (presente em ambos os arquivos)
               - '-' : linha removida (presente apenas no original)
               - '+' : linha adicionada (presente apenas no modificado)
    """

    a_start: int
    a_count: int
    b_start: int
    b_count: int
    lines: list[tuple[str, str]]

    @property
    def old_start(self) -> int:
        """Equivalente a ``a_start`` na convenção old/new."""
        return self.a_start

    @property
    def new_start(self) -> int:
        """Equivalente a ``b_start`` na convenção old/new."""
        return self.b_start


class DiffEngine:
    """
    Motor de diff que fornece várias representações de diferenças entre dois textos.

    retornando diffs nos formatos solicitados.
    """

    @staticmethod
    def unified(
        original: str,
        modified: str,
        context: int = 3,
        original_name: str = "original",
        modified_name: str = "modified",
    ) -> str:
        """
        Retorna um diff unificado (formato tradicional do Unix ``diff -u``).

        Args:
            original: Conteúdo do arquivo original (pode ser None ou vazio).
            modified: Conteúdo do arquivo modificado (pode ser None ou vazio).
            context: Número de linhas de contexto ao redor de cada bloco.
            original_name: Nome exibido para o arquivo original no cabeçalho.
            modified_name: Nome exibido para o arquivo modificado no cabeçalho.

        Returns:
            String contendo o diff unificado (inclui cabeçalhos ``---``/``+++``
            e marcadores ``-``, ``+``, `` ``). Retorna string vazia se não houver
            diferenças ou se ambas as entradas forem vazias.
        """
        original = original or ""
        modified = modified or ""
        orig_lines = original.splitlines(keepends=True)
        mod_lines = modified.splitlines(keepends=True)

        return "".join(
            difflib.unified_diff(
                orig_lines,
                mod_lines,
                fromfile=original_name,
                tofile=modified_name,
                n=context,
            )
        )

    @staticmethod
    def _build_hunk_lines(
        group: list,
        orig_lines: list[str],
        mod_lines: list[str],
    ) -> list[tuple[str, str]]:
        """
        Constrói a lista de linhas anotadas para um grupo de opcodes.

        Args:
            group: Grupo de opcodes retornado por ``SequenceMatcher.get_grouped_opcodes```.
            orig_lines: Linhas do arquivo original.
            mod_lines: Linhas do arquivo modificado.

        Returns:
            Lista de tuplas ``(marcador, texto)`` onde o marcador é
            ``' '`` (contexto), ``'-'`` (removida) ou ``'+'`` (adicionada).
        """
        lines: list[tuple[str, str]] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for line in orig_lines[i1:i2]:
                    lines.append((" ", line))
            elif tag == "replace":
                for line in orig_lines[i1:i2]:
                    lines.append(("-", line))
                for line in mod_lines[j1:j2]:
                    lines.append(("+", line))
            elif tag == "delete":
                for line in orig_lines[i1:i2]:
                    lines.append(("-", line))
            elif tag == "insert":
                for line in mod_lines[j1:j2]:
                    lines.append(("+", line))
        return lines

    @staticmethod
    def hunks(original: str, modified: str, context: int = 3) -> list[DiffHunk]:
        """
        Retorna uma lista de objetos :class:`DiffHunk`, cada um representando
        um bloco de mudanças com linhas de contexto.

        Os índices ``a_start`` e ``b_start`` nos hunks são **1-based**,
        facilitando a exibição em editores de texto ou ferramentas.

        Args:
            original: Conteúdo do arquivo original (pode ser None ou vazio).
            modified: Conteúdo do arquivo modificado (pode ser None ou vazio).
            context: Número de linhas de contexto antes e depois de cada bloco.

        Returns:
            Lista de hunks. Se não houver diferenças, a lista será vazia.
        """
        original = original or ""
        modified = modified or ""
        orig_lines = original.splitlines()
        mod_lines = modified.splitlines()

        matcher = difflib.SequenceMatcher(None, orig_lines, mod_lines)
        hunks: list[DiffHunk] = []

        for group in matcher.get_grouped_opcodes(context):
            a_start = group[0][1]
            a_end = group[-1][2]
            b_start = group[0][3]
            b_end = group[-1][4]
            lines = DiffEngine._build_hunk_lines(group, orig_lines, mod_lines)
            hunks.append(
                DiffHunk(
                    a_start=a_start + 1,
                    a_count=a_end - a_start,
                    b_start=b_start + 1,
                    b_count=b_end - b_start,
                    lines=lines,
                )
            )

        return hunks

    @staticmethod
    def inline_diff(line_a: str, line_b: str) -> list[tuple[str, str]]:
        """
        Realiza diff **intra-linha** (caractere a caractere) entre duas strings.

        Útil para destacar mudanças dentro de uma mesma linha, por exemplo,
        em realces visuais de diferenças.

        Args:
            line_a: Linha original.
            line_b: Linha modificada.

        Returns:
            Lista de tuplas (op, texto) onde ``op`` pode ser:
            - ``'='``: trecho igual em ambas as linhas.
            - ``'-'``: trecho presente apenas em ``line_a``.
            - ``'+'``: trecho presente apenas em ``line_b``.
            Retorna lista vazia se as strings forem idênticas.
        """
        result: list[tuple[str, str]] = []
        matcher = difflib.SequenceMatcher(None, line_a, line_b)

        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                result.append(("=", line_a[i1:i2]))
            elif op == "insert":
                result.append(("+", line_b[j1:j2]))
            elif op == "delete":
                result.append(("-", line_a[i1:i2]))
            elif op == "replace":
                result.append(("-", line_a[i1:i2]))
                result.append(("+", line_b[j1:j2]))

        if all(op == "=" for op, _ in result):
            return []
        return result

    @staticmethod
    def stats(original: str, modified: str) -> tuple[int, int]:
        """
        Calcula estatísticas básicas das diferenças entre dois textos.

        Args:
            original: Conteúdo do arquivo original (pode ser None ou vazio).
            modified: Conteúdo do arquivo modificado (pode ser None ou vazio).

        Returns:
            Tupla ``(added, removed)`` onde:
            - ``added``: número de linhas adicionadas (inserções puras).
            - ``removed``: número de linhas removidas ou modificadas.
        """
        original = original or ""
        modified = modified or ""
        orig_lines = original.splitlines()
        mod_lines = modified.splitlines()

        added = deleted = 0
        matcher = difflib.SequenceMatcher(None, orig_lines, mod_lines)

        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "insert":
                added += j2 - j1
            elif op == "delete":
                deleted += i2 - i1
            elif op == "replace":
                added += j2 - j1
                deleted += i2 - i1

        return added, deleted
