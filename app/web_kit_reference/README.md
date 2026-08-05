# Web Kit — librería de referencia

Colección de animaciones, componentes interactivos y patrones de datos ya
resueltos. La idea: estas son las cosas que más le cuestan a un modelo de IA
generar bien de cero (animaciones con timing correcto, componentes accesibles,
persistencia de datos sin errores). Tenerlas aquí te sirve para dos cosas:

1. **Copiar y pegar** el snippet que necesites en tu proyecto.
2. **Dárselo como referencia al modelo**: "usa el patrón de `componentes/modal.html`
   para hacer el modal de mi web", y el modelo parte de algo que ya funciona en
   vez de inventarlo.

Cada archivo es autónomo: ábrelo con doble clic en el navegador para ver el
efecto, y dentro tiene comentarios explicando cómo funciona y cómo adaptarlo.

## Cómo está organizado

```
web-kit/
├── index.html              ← abre esto primero: índice visual de todo
├── animaciones/
│   ├── scroll-reveal.html      animaciones al hacer scroll (IntersectionObserver)
│   ├── hover-efectos.html      micro-interacciones al pasar el ratón
│   ├── loaders.html            spinners, barras de progreso, skeletons
│   ├── transiciones-css.html   keyframes reutilizables (fade, slide, zoom, etc.)
│   ├── texto-animado.html      typewriter, contador, texto que aparece
│   │   ── nivel "cine / vídeo IA" (context.md sección 15) ──
│   ├── fondo-shader-webgl.html   fondo generativo WebGL, shader propio (15.1)
│   ├── gsap-scrolltrigger.html   GSAP autoalojado: scrub + pin (15.2)
│   ├── rotacion-3d-scroll.html   giro 3D ligado al scroll "Higgsfield" (15.3)
│   ├── scroll-driven-nativo.html animation-timeline: view/scroll, 0 JS (15.4)
│   ├── grano-pelicula.html       grano SVG + color grading de cine (15.5)
│   ├── tipografia-variable.html  peso de fuente que respira con hover/scroll (15.6)
│   └── sonido-click.html         micro-click premium con Web Audio (15.7)
├── 3d/
│   │   ── 3D real con Three.js, no CSS pseudo-3D (context.md 15.8-15.17) ──
│   ├── hero-objeto-flotante.html       objeto flotante con luz real, sigue al puntero (15.8)
│   ├── particulas-atmosfera.html       partículas con profundidad x/y/z real (15.9)
│   ├── producto-configurador-drag.html arrastra para girar el producto, con inercia (15.10)
│   ├── texto-3d-extruido.html          titular como objeto 3D extruido (15.11)
│   ├── galeria-tarjetas-flotantes.html tarjetas flotantes con hover (raycaster) (15.12)
│   ├── globo-wireframe-red.html        planeta wireframe + red de conexiones (15.13)
│   ├── blob-liquido-organico.html      superficie orgánica que muta (shader) (15.14)
│   ├── cristal-material-premium.html   material de cristal con transmisión real (15.15)
│   ├── constelacion-nodos-conexiones.html  conceptos como nodos 3D conectados (15.16)
│   └── cargar-modelo-gltf.html         patrón correcto para cargar un .glb real (15.17)
├── componentes/
│   ├── modal.html              ventana modal accesible
│   ├── acordeon.html           secciones plegables (FAQ)
│   ├── tabs.html               pestañas
│   ├── carrusel.html           slider de imágenes/tarjetas
│   ├── menu-movil.html         menú hamburguesa
│   ├── dropdown.html           menú desplegable
│   ├── tooltip.html            globos de ayuda
│   ├── header-sticky.html      cabecera que cambia al hacer scroll
│   ├── back-to-top.html        botón de volver arriba
│   ├── dark-mode.html          interruptor de modo oscuro (con persistencia)
│   └── formulario.html         formulario con validación en vivo
├── datos/
│   ├── localstorage-crud.html  "base de datos" en el navegador (crear/leer/
│   │                           editar/borrar, persiste al recargar)
│   ├── json-render.html        pintar una lista/grid desde un array de datos
│   └── datos.json              archivo de datos de ejemplo
└── layouts/
    ├── grid-responsive.html    rejilla que se adapta sola
    ├── hero-patrones.html      4 estilos de sección hero
    └── masonry.html            layout tipo Pinterest

```

## Nota sobre "base de datos"

Una web estática (HTML/CSS/JS abierta con doble clic) no tiene una base de
datos de verdad — para eso haría falta un servidor (backend). Lo más parecido
sin servidor es `localStorage`: guarda datos en el propio navegador y persisten
aunque cierres la pestaña. Eso es lo que encontrarás en `datos/localstorage-crud.html`.
Si algún día necesitas una base de datos real (varios usuarios, datos
compartidos), el siguiente paso sería un backend (Node, Supabase, Firebase,
etc.), pero eso ya es otro nivel de proyecto.

## Aviso

Los datos, imágenes (placeholders) y textos son de ejemplo. Las animaciones
respetan `prefers-reduced-motion` donde tiene sentido (accesibilidad).
