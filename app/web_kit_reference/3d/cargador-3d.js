/* ====================================================================
   CARGADOR 3D FOTORREALISTA — la receta completa para que un modelo
   .gltf real se vea FOTO y no "render de juguete", en un solo helper.

   El kit incluye en modelos/ una colección de modelos FOTORREALISTAS
   reales (escaneados/modelados con texturas PBR, de Poly Haven,
   licencia CC0: uso comercial libre sin atribución — ver
   modelos/LICENCIA.md con la tabla negocio → modelo). Escaparate
   visual: catalogo-modelos.html.

   POR QUÉ UN MODELO BUENO PUEDE VERSE MAL: el 90% del fotorrealismo
   no es el modelo, es la LUZ y el COLOR. Un .gltf perfecto con dos
   luces de colores y sin tone mapping se ve a plástico. La receta
   que aplica este helper (y que hay que aplicar SIEMPRE que se
   cargue un .gltf):

   1. scene.environment con un HDRI real (PMREM): el modelo refleja
      un ENTORNO fotográfico completo, no 2-3 puntos de luz. Es la
      diferencia número uno. (modelos/entornos/ trae dos: estudio
      neutro para producto y restaurante cálido para hostelería.)
   2. renderer.toneMapping = ACESFilmicToneMapping: mapa tonal de
      cine — altas luces suaves en vez de blancos quemados.
   3. Sombra de contacto en el suelo: sin sombra, el objeto "flota"
      y el cerebro descarta la foto al instante.
   4. Nada de luces de colores saturados sobre modelos PBR (el truco
      de luz-acento vale para geometría estilizada, con una textura
      fotográfica la tiñe y la estropea).

   CÓMO SE USA — script CLÁSICO + factoría (un módulo ES local no
   carga con doble clic por CORS; este patrón sí):

     <script src="cargador-3d.js"></script>
     <script type="module">
       const THREE = await import('three');
       const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
       const { RGBELoader } = await import('three/addons/loaders/RGBELoader.js');
       const kit3d = window.crearCargador3D(THREE, GLTFLoader, RGBELoader);

       const renderer = kit3d.prepararRenderer(new THREE.WebGLRenderer({ canvas, antialias: true }));
       await kit3d.aplicarEntorno(renderer, scene, 'modelos/entornos/brown_photostudio_02_1k.hdr');
       const tarta = await kit3d.cargar('modelos/carrot_cake/carrot_cake_1k.gltf');
       scene.add(tarta);
     </script>

   AVISO IMPORTANTE — SERVIDOR LOCAL: un .gltf/.hdr local se carga
   con fetch(), y el navegador BLOQUEA fetch de archivos locales en
   páginas abiertas con doble clic (file://). Para ver estos demos:
   VS Code "Live Server", o `python -m http.server` en la carpeta del
   kit. En producción (sitio servido por http/https) funciona solo.
   El verificador del orquestador (render_check) ya navega con el
   flag que lo permite, así que las capturas de verificación salen
   bien igualmente.

   EN PRODUCCIÓN: copiar a assets/ del proyecto SOLO las carpetas de
   los modelos usados (cada una ~1-6MB) + el .hdr del entorno + este
   archivo, y autoalojar three (ver hero-objeto-flotante.html).
==================================================================== */
'use strict';

window.crearCargador3D = function (THREE, GLTFLoader, RGBELoader) {

  /* ── el catálogo local: ruta + para qué negocio (una sola fuente
     de verdad — catalogo-modelos.html y los demos leen de aquí) ── */
  const RUTA_MODELOS = 'modelos/';
  const gltf = (slug) => RUTA_MODELOS + slug + '/' + slug + '_1k.gltf';
  const catalogo = [
    { slug: 'strawberry_chocolate_cake', nombre: 'Tarta de chocolate y fresas', negocios: 'Pastelería, eventos, bodas' },
    { slug: 'carrot_cake', nombre: 'Tarta de zanahoria', negocios: 'Pastelería, cafetería, brunch' },
    { slug: 'croissant', nombre: 'Croissant', negocios: 'Panadería, desayunos, hotel' },
    { slug: 'food_apple_01', nombre: 'Manzana', negocios: 'Frutería, nutrición, dietética' },
    { slug: 'tea_set_01', nombre: 'Juego de té (10 piezas)', negocios: 'Tetería, cafetería — y la vista explosionada' },
    { slug: 'wine_bottles_01', nombre: 'Botellas de vino (4)', negocios: 'Vinoteca, bodega, restaurante' },
    { slug: 'brass_goblets', nombre: 'Copas de latón (3)', negocios: 'Bar premium, coctelería, eventos' },
    { slug: 'wine_barrel_01', nombre: 'Barril de vino', negocios: 'Bodega, taberna, vermutería' },
    { slug: 'bar_chair_round_01', nombre: 'Taburete de bar', negocios: 'Bar, cafetería (atrezzo de escenas)' },
    { slug: 'WoodenTable_02', nombre: 'Mesa de madera', negocios: 'Superficie para componer escenas' },
    { slug: 'potted_plant_01', nombre: 'Planta de interior', negocios: 'Floristería, decoración, wellness' },
    { slug: 'Camera_01', nombre: 'Cámara réflex', negocios: 'Fotografía, bodas, audiovisual' },
    { slug: 'classic_laptop', nombre: 'Portátil', negocios: 'Agencia, software, academia' },
    { slug: 'book_encyclopedia_set_01', nombre: 'Enciclopedias', negocios: 'Librería, editorial, despacho' },
    { slug: 'BarberShopChair_01', nombre: 'Sillón de barbero', negocios: 'Barbería, peluquería' },
    { slug: 'CashRegister_01', nombre: 'Caja registradora', negocios: 'Tienda, retail con encanto' },
  ].map((m) => Object.assign(m, { ruta: gltf(m.slug) }));

  const ENTORNOS = {
    estudio: RUTA_MODELOS + 'entornos/brown_photostudio_02_1k.hdr',      // neutro: producto
    restaurante: RUTA_MODELOS + 'entornos/warm_restaurant_night_1k.hdr', // cálido: hostelería
  };

  /* ── 1. renderer con color de cine ── */
  function prepararRenderer(renderer) {
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping; // altas luces de foto, no quemadas
    renderer.toneMappingExposure = 1.0;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;   // sombras de borde suave
    return renderer;
  }

  /* ── 2. entorno HDRI: la fuente de luz PRINCIPAL de un modelo PBR ──
     opciones.fondo: true = además se VE de fondo (con
     opciones.desenfoque 0..1, desenfocado queda a "foto con poca
     profundidad de campo" — perfecto detrás de un producto). */
  async function aplicarEntorno(renderer, scene, url, opciones) {
    opciones = opciones || {};
    const hdr = await new RGBELoader().loadAsync(url);
    hdr.mapping = THREE.EquirectangularReflectionMapping;
    scene.environment = hdr;
    if (opciones.fondo) {
      scene.background = hdr;
      scene.backgroundBlurriness = opciones.desenfoque === undefined ? 0 : opciones.desenfoque;
      scene.backgroundIntensity = opciones.intensidadFondo === undefined ? 1 : opciones.intensidadFondo;
    }
    return hdr;
  }

  /* ── 3. cargar + normalizar: centrado en el origen, dimensión mayor
     = opciones.alto (default 2). El contrato de siempre: cualquier
     demo puede intercambiar modelos sin tocar cámara ni encuadre.
     Deja en userData.piezas las mallas del modelo con su posición
     original (para vista explosionada / animar piezas sueltas). ── */
  async function cargar(url, opciones) {
    opciones = opciones || {};
    const resultado = await new GLTFLoader().loadAsync(url, opciones.progreso);
    const objeto = resultado.scene;

    const piezas = [];
    objeto.traverse((n) => {
      if (n.isMesh) {
        n.castShadow = true;
        n.receiveShadow = true;
        // centro GEOMÉTRICO de la pieza (no la posición del nodo: en
        // muchos exports todas las mallas están en el origen con la
        // colocación horneada en los vértices) — es lo que necesita
        // una vista explosionada para saber hacia dónde huye cada una
        n.geometry.computeBoundingBox();
        const centroPieza = n.geometry.boundingBox.getCenter(new THREE.Vector3()).add(n.position);
        piezas.push({ malla: n, posicionBase: n.position.clone(), centro: centroPieza, nombre: n.name });
      }
    });

    const caja = new THREE.Box3().setFromObject(objeto);
    const centro = caja.getCenter(new THREE.Vector3());
    const tamano = caja.getSize(new THREE.Vector3());
    const escala = (opciones.alto || 2) / (Math.max(tamano.x, tamano.y, tamano.z) || 1);
    objeto.scale.setScalar(escala);
    objeto.position.copy(centro).multiplyScalar(-escala);

    const envoltorio = new THREE.Group();
    envoltorio.add(objeto);
    // tamanoOriginal/escala: las piezas viven en unidades ORIGINALES
    // del archivo (metros reales) — cualquier desplazamiento de piezas
    // debe medirse contra tamanoOriginal, no contra `alto`
    envoltorio.userData = { nombre: url, piezas, tamanoOriginal: tamano, escala };
    return envoltorio;
  }

  /* ── 4a. luz clave con sombra: el HDRI ilumina pero no proyecta
     sombras nítidas — esta direccional (suave, CÁLIDA-NEUTRA, nunca
     de color saturado) existe casi solo para la sombra de contacto ── */
  function luzClave(scene, opciones) {
    opciones = opciones || {};
    const luz = new THREE.DirectionalLight(opciones.color || 0xfff4e6, opciones.intensidad || 1.6);
    luz.position.set(3, 5, 3.5);
    luz.castShadow = true;
    luz.shadow.mapSize.set(2048, 2048);
    luz.shadow.camera.near = 0.5;
    luz.shadow.camera.far = 20;
    // el volumen de sombra justo alrededor del objeto: más área = más borroso
    luz.shadow.camera.left = luz.shadow.camera.bottom = -(opciones.area || 4);
    luz.shadow.camera.right = luz.shadow.camera.top = (opciones.area || 4);
    luz.shadow.bias = -0.0004; // evita el acné de sombra en superficies curvas
    scene.add(luz);
    return luz;
  }

  /* ── 4b. suelo que SOLO recibe sombra (ShadowMaterial): la sombra
     de contacto flota sobre el fondo CSS/HDRI que haya debajo ── */
  function sueloSombra(scene, opciones) {
    opciones = opciones || {};
    const suelo = new THREE.Mesh(
      new THREE.CircleGeometry(opciones.radio || 7, 48),
      new THREE.ShadowMaterial({ opacity: opciones.opacidad === undefined ? 0.32 : opciones.opacidad }));
    suelo.rotation.x = -Math.PI / 2;
    suelo.position.y = opciones.y || 0;
    suelo.receiveShadow = true;
    scene.add(suelo);
    return suelo;
  }

  /* ── utilidades de composición ── */
  function apoyarEnSuelo(modelo) { // baja el modelo hasta que su base toque y=0
    const caja = new THREE.Box3().setFromObject(modelo);
    modelo.position.y -= caja.min.y;
    return modelo;
  }

  return { catalogo, ENTORNOS, prepararRenderer, aplicarEntorno, cargar, luzClave, sueloSombra, apoyarEnSuelo };
};
