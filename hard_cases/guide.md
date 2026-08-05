# Guia de uso de la base de casos dificiles

Esta base NO es documentacion general - es una coleccion curada de
situaciones puntuales donde modelos de 7B-24B suelen equivocarse.
Consultarla tiene costo (latencia + un embedding call), asi que no se
consulta en cada request.

## Cuando consultar
Evalua estas señales ANTES de generar tu respuesta final. Si al menos
una aplica, formula una query:
- La tarea toca: concurrencia/async, NULL o logica de tres valores,
  lifetimes/borrowing, manejo de errores en paralelo, generacion de SQL
  dinamico, infraestructura como codigo con recursos que pueden mutar
  con el tiempo, o cualquier operacion que toque datos de usuario sin
  sanitizar.
- Detectaste que tu propia solucion usa un patron "clasico de tutorial"
  (ej. Promise.all, NOT IN subquery, count en Terraform, default
  mutable) - estos son precisamente los patrones sobrerrepresentados
  en el corpus que a veces esconden un caso limite.
- El nivel de riesgo señalado por el router (risk_signals) incluye
  auth, concurrency, o escritura de datos.

Si ninguna señal aplica, NO consultes - genera directo.

## Como formular la query
Usa lenguaje descriptivo del *patron*, no del codigo literal:
mal:  "def f(x=[])"
bien: "argumento por defecto mutable en funcion python persistiendo entre llamadas"

Incluí el lenguaje/dominio como filtro (language_or_domain) ademas del
texto libre - la busqueda es hibrida (semantica + filtro).

## Como usar lo recuperado
- Trátalo como contexto de razonamiento interno, citando el id en tu
  traza si te hizo cambiar de enfoque.
- NO copies el good_example literal si no calza exacto con el caso del
  usuario - adapta el patron, no el texto.
- Si hay conflicto entre lo recuperado y una instruccion explicita del
  usuario, prioriza la instruccion del usuario pero advierte el riesgo
  en tu explicacion.

## Si no encuentras nada relevante
No inventes que existe un caso documentado. Continua con tu propio
razonamiento y dilo explicitamente en la traza (hard_cases_consulted
vacio). Un falso "segun el caso documentado X..." es peor que no citar
nada.
