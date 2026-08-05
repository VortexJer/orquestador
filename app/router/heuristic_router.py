"""Router heuristico (deterministico, sin LLM) - implementacion por
defecto en fase 1, mientras no hay GPU disponible para correr el router
real (Qwen2.5-1.5B-Instruct). Interfaz identica a LLMRouter: cambiar de
uno a otro es una linea de config (ver app/router/base.py), no un
cambio del core.

Es intencionalmente simple y 100% testeable sin dependencias externas -
por eso sirve como baseline medible para eval/scripts/eval_router.py
(criterio de aceptacion de fase 1 pedido por el usuario).
"""
from __future__ import annotations

import re

from app.config import get_enabled_specialists, get_specialist
from app.schemas import Constraints, Domain, EffortLevel, GenerateRequest, RoutingDecision

# Orden de prioridad: si varias reglas matchean, gana la primera de la lista.
# Seguridad va primero porque un patron de codigo con riesgo de seguridad
# debe tratarse como tal aunque tambien sea python/sql valido (ver
# eval/router_eval_set.json, casos "edge-01/02/03" que ejercitan
# deliberadamente estas colisiones de prioridad).
_DOMAIN_PATTERNS: list[tuple[Domain, re.Pattern]] = [
    ("security", re.compile(
        r"\b(vulnerab\w*|cve-|sql injection|inyecci[oó]n sql|xss|sanitiz\w*|"
        r"secreto expuesto|hardcoded password|path traversal|csrf)\b", re.I)),
    ("iac", re.compile(
        r"\b(terraform|\.tf\b|resource\s+\"|provider\s+\"|helm chart|kubernetes yaml|k8s manifest)\b", re.I)),
    # El nombre del lenguaje a secas ("en Rust", "una clase Kotlin") es una
    # señal de dominio tan fuerte como cualquier keyword de sintaxis, y es
    # como pide ayuda la mayoria de la gente ("tengo un problema en Rust"),
    # sin pegar codigo. Sin el, esos pedidos caian todos al default python.
    ("rust", re.compile(r"\b(cargo|fn\s+main|impl\s+\w+ for|tokio::|borrow checker|lifetime|rust)\b", re.I)),
    # "func\s+\w+\(" queda fuera del grupo \b...\b a proposito: el \b de
    # cierre no puede afirmarse cuando el caracter siguiente al "(" es
    # otro no-alfanumerico (ej. "func f()" con parentesis vacios) - un
    # \b pegado a puntuacion en ambos lados nunca matchea ahi.
    # "golang" (no "go" a secas: "go" es una palabra corta que aparece
    # dentro de frases en ingles y en falsos amigos; "golang" es inequivoco).
    ("go", re.compile(r"\b(package main|goroutine|go\.mod|:=\s|golang)\b|\bfunc\s+\w+\(", re.I)),
    # "c++" y "c#" van fuera del grupo \b...\b porque terminan en un caracter
    # no-alfanumerico (+/#): un \b de cierre pegado a puntuacion no se afirma.
    ("cpp", re.compile(r"\b(#include|nullptr|std::\w+|unique_ptr|shared_ptr|\.cpp\b|\.hpp\b)\b|\bc\+\+", re.I)),
    ("csharp", re.compile(
        r"\b(using System|Console\.WriteLine|async Task|namespace\s+\w+|public class|\.cs\b)\b|\bc#", re.I)),
    # \bjava\b NO matchea dentro de "javascript" (no hay frontera entre 'a' y
    # 's'), asi que es seguro tener java antes que javascript en la lista.
    ("java", re.compile(
        r"\b(public class|public static void main|System\.out\.println|import java\.\w+|\.java\b|java)\b", re.I)),
    ("kotlin", re.compile(
        r"\b(fun\s+main|companion object|data class|suspend fun|\.kt\b|\.kts\b|kotlin)\b", re.I)),
    # accessibility ANTES que web a proposito: "accesibilidad/WCAG/ARIA" es una
    # señal MAS especifica que "pagina web". Un pedido de accesibilidad SOBRE una
    # web ("mejora la accesibilidad WCAG de esta pagina") es accesibilidad, no un
    # encargo de construir un sitio. Esto no amplia el regex - reordena por
    # especificidad, y ademas arregla el unico fallo del eval de fase 1 (edge-06).
    # "aria-" va FUERA del grupo \b...\b: termina en '-' (no-alfanumerico), y un
    # \b de cierre pegado a un guion nunca se afirma. Suelto, matchea "aria-label".
    ("accessibility", re.compile(
        r"\b(accesibilidad|accesible|wcag|a11y|lector de pantalla|contraste|screen reader)\b|\baria-", re.I)),
    # "web" = construccion de sitios completos (HTML/CSS + contenido real
    # via scraping/busqueda), distinto de "javascript/typescript" que son
    # tareas de logica de codigo en ese lenguaje sin necesariamente armar
    # una pagina entera.
    # Segundo grupo: cubre la forma MAS comun en español de pedir un sitio
    # ("hazme una web", "necesito una web", "quiero una web...") sin usar
    # las frases mas especificas de arriba - encontrado como bug real
    # probando la terminal: "hazme una web para una carniceria" no
    # matcheaba nada de esto y caia en python por default.
    ("web", re.compile(
        r"\b(sitio web|p[aá]gina web|landing page|responsive|maquetado|diseño web|\.html\b|\.css\b)\b|"
        r"\b(haz\w*|crea\w*|arma\w*|prepara\w*|necesit\w*|quier\w*|dame)\s+(hacer\s+)?una\s+web\b", re.I)),
    # \bsql\b no matchea dentro de "mysql"/"postgresql" (la frontera cae mal),
    # asi que nombrar el lenguaje no dispara con esos motores por accidente.
    ("sql", re.compile(r"\b(select\s+.+from|insert into|create table|update\s+\w+\s+set|left join|sql)\b", re.I)),
    # "=>" y "->" quedan fuera del grupo \b...\b: son puros caracteres de
    # puntuacion, un \b en cualquiera de sus extremos casi nunca se cumple.
    # console.log se dejo deliberadamente fuera de este patron (es una API
    # de runtime, no una señal de tipado) - vive en el patron de javascript,
    # que se revisa despues como bucket mas generico.
    ("typescript", re.compile(
        r"\b(interface\s+\w+|useState|useEffect|\.tsx?\b|npm install|typescript)\b|=>", re.I)),
    # "require\(" queda fuera del grupo \b...\b: si el caracter siguiente
    # al "(" es otro no-alfanumerico (ej. require("fs"), donde sigue una
    # comilla), el \b de cierre no puede afirmarse - mismo patron de bug
    # que func\w+\( en go.
    # "javascript" a secas es seguro aqui: typescript se revisa ANTES en la
    # lista, asi que "TypeScript" ya se llevo su caso y no cae en este.
    ("javascript", re.compile(
        r"\b(module\.exports|document\.querySelector|getElementById|console\.log|\.js\b|javascript)\b|\brequire\(", re.I)),
    ("php", re.compile(
        r"\b(public function|array_map\(|str_contains\(|\.php\b|php)\b|<\?php|\$this->", re.I)),
    ("ruby", re.compile(
        r"\b(attr_accessor|attr_reader|elsif|require_relative|puts|\.rb\b|ruby)\b", re.I)),
    ("testing", re.compile(r"\b(escribe (los )?tests?|unit tests?|test coverage|casos de prueba)\b", re.I)),
    ("refactor", re.compile(r"\b(refactoriza|refactor|limpia (este|el) c[oó]digo|elimina duplicaci[oó]n)\b", re.I)),
    ("docs", re.compile(r"\b(documenta\w*|escribe (un )?readme|docstring)\b", re.I)),
    ("office_word", re.compile(
        r"(arma|necesito|genera|crea|prepara)\s+(un\s+)?(documento\s+)?word\b|documento word|\.docx\b", re.I)),
    ("office_spreadsheet", re.compile(
        r"(arma|necesito|genera|crea|prepara)\s+(un[a]?\s+)?(excel|xlsx|hoja de c[aá]lculo|"
        r"planilla de c[aá]lculo)\b|\.xlsx\b", re.I)),
    ("office_presentation", re.compile(
        r"(arma|necesito|genera|crea|prepara)\s+(un[a]?\s+)?(power ?point|presentaci[oó]n|diapositivas)\b|"
        r"\.pptx\b", re.I)),
    # Requiere un verbo de redaccion + objeto (no solo la palabra "email"/
    # "correo" suelta, que tambien aparece en tareas de codigo legitimas
    # como "valida un email" - ver tests/test_gateway.py).
    ("office_email", re.compile(
        r"\b(redacta (un )?(correo|email|carta)|escribe (un )?(correo|email)|"
        r"carta de presentaci[oó]n)\b", re.I)),
    ("trivial_text", re.compile(r"\b(resume(n)? (esto|el texto)|informe breve)\b", re.I)),
    # Los acentos van como escapes unicode (\xf3 = o con tilde) a proposito:
    # escritos como caracteres literales se corrompieron al generar este
    # archivo desde un script, y el patron compilaba pero no matcheaba
    # NUNCA - un fallo silencioso que solo se ve probando el router.
    ("code_review", re.compile(
        r"\b(revisa\w*|revisi\xf3n|revision|audita\w*)\b[^.]{0,20}\b(c\xf3digo|codigo|pr|cambio|commit|diff)\b|"
        r"\bcode review\b", re.I)),
    ("debugging", re.compile(
        # "falla" a secas queda FUERA a proposito: aparece en descripciones
        # normales de una tarea de lenguaje ("este import de pandas falla"),
        # y ahi lo que hace falta es el especialista del lenguaje, que sabe
        # de pandas, no metodologia generica de diagnostico. Se exige una
        # señal mas fuerte de "no se por que pasa esto".
        r"\b(no funciona|no anda|da (un |este )?error|por qu\xe9 (falla|peta|revienta)|"
        r"depura\w*|debug\w*|stack ?trace|traceback|no s\xe9 por qu\xe9)\b", re.I)),
    # Investigacion OSINT de un SUJETO del mundo real (persona/empresa/marca):
    # reunir y contrastar fuentes, "huella digital", dossier. Va casi al final
    # (antes de python) para que un lenguaje/oficina concreto gane primero.
    # A proposito NO matchea "investiga por que falla X" (investiga + por que):
    # eso es un fallo de codigo, va a su lenguaje/debugging. Exige que el verbo
    # de investigar apunte a un SUJETO (a/sobre/la huella de...) o un termino
    # inequivoco de investigacion (huella digital, osint, dossier).
    ("research", re.compile(
        r"\b(huella digital|osint|due diligence|dossier|reputaci[oó]n (online|digital|en internet)|"
        r"qu[eé] se sabe (de|sobre)|perfil (p[uú]blico|online|digital)|antecedentes de)\b|"
        r"\b(investiga\w*|averigua\w*|indaga\w*|recopila\w*)\s+(todo\s+)?"
        r"(a|sobre|acerca|la\s+huella|el\s+perfil|informaci[oó]n\s+(de|sobre))\b|"
        r"\b(investigaci[oó]n|dossier)\s+(sobre|de|acerca|completa)\b", re.I)),
    ("python", re.compile(r"\b(def\s+\w+\(|import\s+\w+|self\.|\.py\b|pytest|python)\b", re.I)),
]

_RISK_PATTERNS = {
    "concurrency": re.compile(r"\b(async|await|goroutine|thread|concurren\w*|paralel\w*|race condition)\b", re.I),
    "auth": re.compile(r"\b(auth|login|password|jwt|token de sesi[oó]n|permisos)\b", re.I),
    "sql_write": re.compile(r"\b(insert into|update\s+\w+\s+set|delete from)\b", re.I),
}

# Fallback cuando el dominio detectado no tiene un especialista habilitado
# todavia (fase 1 solo tiene python-specialist y generalist-tiny activos).
# El fallback depende de la FAMILIA del dominio: un especialista de codigo
# ausente degrada a python-specialist (sigue siendo una tarea de codigo,
# aunque no en el lenguaje ideal); un especialista de oficina ausente
# degrada a generalist-tiny (no tiene sentido invocar un modelo de codigo
# de 14B para redactar un correo).
_CODE_FALLBACK = "python-specialist"
_OFFICE_FALLBACK = "generalist-tiny"

DOMAIN_TO_SPECIALIST_HINT = {
    "python": "python-specialist",
    "code_review": "code-review-specialist",
    "debugging": "debugging-specialist",
    "accessibility": "accessibility-specialist",
    "testing": "testing-specialist",
    "refactor": "refactor-specialist",
    "typescript": "typescript-specialist",
    "javascript": "javascript-specialist",
    "java": "java-specialist",
    "csharp": "csharp-specialist",
    "cpp": "cpp-specialist",
    "sql": "sql-specialist",
    "security": "security-specialist",
    "iac": "devops-iac-specialist",
    "rust": "rust-specialist",
    "go": "go-specialist",
    "php": "php-specialist",
    "ruby": "ruby-specialist",
    "kotlin": "kotlin-specialist",
    "web": "web-builder-specialist",
    "research": "research-specialist",
    "docs": "docs-specialist",
    "office_email": "office-email-specialist",
    "office_word": "office-word-specialist",
    "office_spreadsheet": "office-spreadsheet-specialist",
    "office_presentation": "office-presentation-specialist",
    "trivial_text": "generalist-tiny",
}

_DOMAIN_FALLBACK_FAMILY = {
    "office_email": _OFFICE_FALLBACK,
    "office_word": _OFFICE_FALLBACK,
    "office_spreadsheet": _OFFICE_FALLBACK,
    "office_presentation": _OFFICE_FALLBACK,
    "trivial_text": _OFFICE_FALLBACK,
    "docs": _OFFICE_FALLBACK,
}


def match_domain_or_none(task: str, language_hint: str | None = None) -> Domain | None:
    """Igual que classify_domain pero SIN el default a 'python' - devuelve
    None si ningun patron matcheo. Existe para que un caller con memoria
    de sesion (ej. groq_agent/cli.py) pueda distinguir "esta tarea es
    genuinamente python" de "no dijo nada clasificable (saludo, 'si',
    'ok') y python es solo el relleno por defecto" - en el segundo caso
    conviene seguir con el especialista de la tarea anterior en vez de
    reiniciar la conversacion con python-specialist sin venir a cuento."""
    text = f"[{language_hint}] {task}" if language_hint else task
    for candidate_domain, pattern in _DOMAIN_PATTERNS:
        if pattern.search(text):
            return candidate_domain
    return None


def classify_domain(task: str, language_hint: str | None = None) -> tuple[Domain, list[str]]:
    """Solo la clasificacion (dominio + señales de riesgo), sin la logica
    de "que especialista de PRODUCCION esta habilitado" - reusable por
    cualquier cosa que necesite saber el dominio de una tarea sin
    importarle el gating de config/specialists.yaml (ej. el auto-routing
    de groq_agent, que puede usar CUALQUIER skill del catalogo)."""
    text = f"[{language_hint}] {task}" if language_hint else task

    domain: Domain = match_domain_or_none(task, language_hint) or "python"
    risk_signals = [name for name, pattern in _RISK_PATTERNS.items() if pattern.search(text)]
    return domain, risk_signals


class HeuristicRouter:
    """Implementa app.router.base.Router sin heredar formalmente para
    evitar overhead de ABC en el hot path; el contrato es el mismo."""

    def route(self, request: GenerateRequest, currently_warm_ref: str | None = None) -> RoutingDecision:
        domain, risk_signals = classify_domain(request.task, request.language_hint)

        enabled = get_enabled_specialists()
        hinted = DOMAIN_TO_SPECIALIST_HINT.get(domain, _CODE_FALLBACK)
        if hinted in enabled:
            specialist_name = hinted
        else:
            specialist_name = _DOMAIN_FALLBACK_FAMILY.get(domain, _CODE_FALLBACK)
            risk_signals = [*risk_signals, f"domain_specialist_unavailable:{domain}"]

        spec_cfg = get_specialist(specialist_name)
        reused_warm_model = bool(
            currently_warm_ref and spec_cfg.get("base_model_ref") == currently_warm_ref
        )

        effort_level = self._effort_level(request, risk_signals)

        return RoutingDecision(
            domain=domain,
            subtasks=[],
            effort_level=effort_level,
            risk_signals=risk_signals,
            specialists_to_invoke=[specialist_name],
            reused_warm_model=reused_warm_model,
        )

    def _effort_level(self, request: GenerateRequest, risk_signals: list[str]) -> EffortLevel:
        constraints: Constraints = request.constraints
        if constraints.quality_level == "draft":
            return "quick_pass"
        if risk_signals or request.context.files:
            return "iterative_verify"
        if len(request.task) < 80:
            return "quick_pass"
        return "standard"

    def validate(self, request: GenerateRequest, candidate_content: str, tools_passed: bool) -> tuple[bool, str]:
        """Validacion rule-based (fase 1, sin LLM router todavia). Cuando el
        router real (LLM) este online, esta misma responsabilidad la
        cumple una llamada corta al mismo modelo ya cargado - ver
        app/validation/final_validator.py.
        """
        if not candidate_content.strip():
            return False, "La respuesta esta vacia."
        if not tools_passed:
            return False, "Las herramientas de verificacion no pasaron en la ultima iteracion."
        return True, "Respuesta no vacia y herramientas en verde."
