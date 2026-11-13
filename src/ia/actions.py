"""Codis IA - Sistema de Agent Skills.

Cada skill define como o agente de IA deve se comportar para um tipo de tarefa:
prompt de sistema dedicado, prefixo de prompt, tags de categorização, configuração
de contexto recomendada e metadados de apresentação.

Arquitetura
-----------
A skill é definida em arquivos ``.md`` com blocos estruturados. O ``SkillRegistry``
descobre, valida, indexa e serve as skills com substituição pelo usuário.

Ordem de prioridade de carregamento
------------------------------------
1. ``~/.codis/skills/<id>.md``              - skills do usuário (maior prioridade)
2. ``src/ia/skills_default/<id>.md``         - skills embutidas no projeto

Formato do arquivo ``.md``
---------------------------

    ```yaml
    id: fix
    i18n_key: ia.skill_fix
    version: "1.0"
    author: "Codis"
    tags: [debug, correction, quality]
    prompt_prefix: "Identifique e corrija bugs no seguinte código:\\n\\n"
    requires_context: recommended
    output_format: code+explanation
    ```

Seções reconhecidas no corpo do arquivo:

    ## Prompt de sistema      - system prompt específico desta skill (obrigatório)
    ## Descrição              - descrição longa (usada na UI de gerenciamento)
    ## Exemplos de uso        - exemplos de prompts para exibir na UI
    ## Configuração           - notas sobre contexto recomendado
    ## Notas                  - informações adicionais

Campos do bloco YAML
--------------------
id                : Identificador único da skill (ex: ``"fix"``).
i18n_key          : Chave de tradução exibida na UI (ex: ``"ia.skill_fix"``).
version           : Versão semântica para controle pessoal (ex: ``"1.0"``).
author            : Autor da skill (ex: ``"Codis"`` ou nome do usuário).
tags              : Lista de tags para agrupamento e filtro (ex: ``[debug, quality]``).
prompt_prefix     : Texto inserido antes do prompt do usuário ao enviar ao modelo.
requires_context  : Contexto recomendado - ``none``, ``recommended`` ou ``required``.
output_format     : Formato esperado de saída - ``code``, ``text``, ``code+explanation``.

Uso típico
----------
    from src.ia.actions import list_skills

    for skill in list_skills():
        print(skill.id, skill.prompt_prefix, skill.tags)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("Codis.IA.Skills")

_DEFAULT_SKILLS_DIR = Path(__file__).parent / "skills_default"

_USER_SKILLS_DIR = Path.home() / ".codis" / "skills"

_FALLBACK_SYSTEM_PROMPT = ""

_RE_YAML_BLOCK = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)

_RE_SYSTEM_SECTION = re.compile(
    r"^##\s+Prompt de sistema\s*\n(.*?)(?=^##|\Z)",
    re.MULTILINE | re.DOTALL,
)

_RE_DESC_SECTION = re.compile(
    r"^##\s+Descri[cç][aã]o\s*\n(.*?)(?=^##|\Z)",
    re.MULTILINE | re.DOTALL,
)

_RE_EXAMPLES_SECTION = re.compile(
    r"^##\s+Exemplos de uso\s*\n(.*?)(?=^##|\Z)",
    re.MULTILINE | re.DOTALL,
)

_RE_TAGS_INLINE = re.compile(r"\[([^\]]*)\]")


@dataclass
class Skill:
    """Representa uma agent skill do assistente de IA do Codis.

    Atributos
    ---------
    id              : Identificador único (ex: ``"fix"``).
    i18n_key        : Chave de tradução para a UI (ex: ``"ia.skill_fix"``).
    version         : Versão da skill (ex: ``"1.0"``).
    author          : Autor da skill.
    tags            : Tags para agrupamento e filtro (ex: ``["debug", "quality"]``).
    prompt_prefix   : Texto inserido antes do prompt do usuário.
    system_prompt   : System prompt completo enviado ao modelo para esta skill.
    description     : Descrição longa exibida na UI de gerenciamento.
    examples        : Exemplos de uso exibidos na UI (lista de strings).
    requires_context: Nível de contexto recomendado - ``"none"``, ``"recommended"``, ``"required"``.
    output_format   : Formato esperado - ``"code"``, ``"text"``, ``"code+explanation"``.
    source          : Caminho do arquivo ``.md`` de origem.
    is_user_skill   : True se carregada do diretório do usuário.
    """

    id: str
    i18n_key: str
    version: str = "1.0"
    author: str = "Codis"
    tags: list[str] = field(default_factory=list)
    prompt_prefix: str = ""
    system_prompt: str = _FALLBACK_SYSTEM_PROMPT
    description: str = ""
    examples: list[str] = field(default_factory=list)
    requires_context: str = "recommended"
    output_format: str = "code+explanation"
    source: Path | None = field(default=None, repr=False)
    is_user_skill: bool = False

    @property
    def display_name(self) -> str:
        """Nome de exibição compacto (id com primeira letra maiúscula)."""
        return self.id.replace("_", " ").title()


class SkillRegistry:
    """Descobre, valida, indexa e serve as agent skills do Codis.

    Suporta substituição por skills do usuário, filtragem por tag,
    e recarregamento do disco sob demanda.

    Ordem de prioridade de carregamento
    ------------------------------------
    1. ``~/.codis/skills/<id>.md``          (usuário - maior prioridade)
    2. ``src/ia/skills_default/<id>.md``    (padrão embutido)
    """

    def __init__(self) -> None:
        """Inicializa o registro de skills de IA."""
        self._skills: dict[str, Skill] = {}
        self._loaded: bool = False

    @staticmethod
    def _ensure_user_dir() -> None:
        """Cria o diretório de skills do usuário se não existir."""
        try:
            _USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Não foi possível criar ~/.codis/skills/: %s", exc)

    @staticmethod
    def _parse_yaml_simple(text: str) -> dict[str, str]:
        """Extrai pares chave: valor de um bloco YAML simples (sem dependência externa)."""
        result: dict[str, str] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, _, raw_value = line.partition(":")
            key = key.strip()
            raw_value = raw_value.strip().strip('"').strip("'")
            if key:
                result[key] = raw_value
        return result

    @staticmethod
    def _parse_tags(raw: str) -> list[str]:
        """Extrai lista de tags de um valor YAML como ``[a, b, c]`` ou ``a, b``."""
        m = _RE_TAGS_INLINE.search(raw)
        if m:
            raw = m.group(1)
        return [t.strip().lower() for t in raw.split(",") if t.strip()]

    @staticmethod
    def _parse_examples(section_text: str) -> list[str]:
        """Extrai itens de lista Markdown (linhas que começam com ``- ``)."""
        examples = []
        for line in section_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                examples.append(stripped[2:].strip())
        return examples

    def _load_md(self, path: Path, is_user: bool = False) -> Skill | None:
        """Carrega uma skill a partir de um arquivo ``.md``.

        Retorna ``None`` se o arquivo estiver ausente ou mal formatado.
        """
        try:
            content = path.read_text("utf-8")
        except OSError as exc:
            logger.warning("Falha ao ler a skill '%s': %s", path, exc)
            return None

        m_yaml = _RE_YAML_BLOCK.search(content)
        if not m_yaml:
            logger.debug("Skill '%s' sem bloco YAML - ignorada.", path.name)
            return None

        meta = self._parse_yaml_simple(m_yaml.group(1))
        skill_id = meta.get("id", path.stem)
        i18n_key = meta.get("i18n_key", f"ia.skill_{skill_id}")
        version = meta.get("version", meta.get("versao", "1.0"))
        author = meta.get("author", meta.get("autor", "Codis"))
        prefix = meta.get("prompt_prefix", "").replace("\\n", "\n")
        req_ctx = meta.get("requires_context", "recommended")
        out_fmt = meta.get("output_format", "code+explanation")
        tags_raw = meta.get("tags", "")
        tags = self._parse_tags(tags_raw)

        m_sys = _RE_SYSTEM_SECTION.search(content)
        system_prompt = m_sys.group(1).strip(
        ) if m_sys else _FALLBACK_SYSTEM_PROMPT

        m_desc = _RE_DESC_SECTION.search(content)
        description = m_desc.group(1).strip() if m_desc else ""

        m_ex = _RE_EXAMPLES_SECTION.search(content)
        examples = self._parse_examples(m_ex.group(1)) if m_ex else []

        return Skill(
            id=skill_id,
            i18n_key=i18n_key,
            version=version,
            author=author,
            tags=tags,
            prompt_prefix=prefix,
            system_prompt=system_prompt,
            description=description,
            examples=examples,
            requires_context=req_ctx,
            output_format=out_fmt,
            source=path,
            is_user_skill=is_user,
        )

    def _load_all(self) -> None:
        """Carrega todas as skills disponíveis (padrão + substituições do usuário)."""
        self._skills.clear()
        self._ensure_user_dir()

        if _DEFAULT_SKILLS_DIR.exists():
            for md in sorted(_DEFAULT_SKILLS_DIR.glob("*.md")):
                skill = self._load_md(md, is_user=False)
                if skill:
                    self._skills[skill.id] = skill
                    logger.debug("Skill padrão carregada: %s", skill.id)

        if _USER_SKILLS_DIR.exists():
            for md in sorted(_USER_SKILLS_DIR.glob("*.md")):
                skill = self._load_md(md, is_user=True)
                if skill:
                    if skill.id in self._skills:
                        logger.debug(
                            "Skill '%s' substituída pela versão do usuário.", skill.id
                        )
                    else:
                        logger.debug(
                            "Skill do usuário carregada: %s", skill.id)
                    self._skills[skill.id] = skill

        self._loaded = True
        n_default = sum(1 for s in self._skills.values()
                        if not s.is_user_skill)
        n_user = sum(1 for s in self._skills.values() if s.is_user_skill)
        logger.info(
            "%d skills carregadas (%d padrão + %d do usuário).",
            len(self._skills),
            n_default,
            n_user,
        )

    def _ensure_loaded(self) -> None:
        """Garante que as skills foram carregadas ao menos uma vez."""
        if not self._loaded:
            self._load_all()

    def list_all(self) -> list[Skill]:
        """Retorna todas as skills carregadas, ordenadas por id."""
        self._ensure_loaded()
        return sorted(self._skills.values(), key=lambda s: s.id)

skill_registry: SkillRegistry = SkillRegistry()


def list_skills() -> list[Skill]:
    """Atalho de módulo: retorna todas as skills disponíveis."""
    return skill_registry.list_all()
