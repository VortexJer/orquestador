# Modelos 3D fotorrealistas — licencia y origen

Todos los modelos de esta carpeta (y los entornos de `entornos/`) vienen de
**Poly Haven** (https://polyhaven.com) con licencia **CC0** (dominio público):
se pueden usar, modificar y redistribuir **sin atribución, también en
proyectos comerciales** (webs de clientes incluidas). Es la única fuente que
usamos precisamente por eso — nada de "royalty free con condiciones".

Cada carpeta es un modelo en formato glTF (`.gltf` + `.bin` + `textures/`),
resolución de texturas 1k (suficiente para web; si un proyecto necesita un
primer plano gigante, en polyhaven.com está el mismo modelo en 2k/4k/8k).

| Carpeta | Qué es | Negocios típicos |
|---|---|---|
| carrot_cake | Tarta de zanahoria con un corte | Pastelería, cafetería, brunch |
| strawberry_chocolate_cake | Tarta de chocolate con fresas | Pastelería, eventos, bodas |
| croissant | Croissant | Panadería, desayunos, hotel |
| food_apple_01 | Manzana | Frutería, nutrición, dietética |
| tea_set_01 | Juego de té (10 piezas separadas) | Tetería, cafetería — y la vista explosionada |
| wine_bottles_01 | 4 botellas de vino distintas (piezas separadas) | Vinoteca, bodega, restaurante |
| brass_goblets | 3 copas de latón | Bar premium, restaurante, eventos |
| wine_barrel_01 | Barril de vino | Bodega, taberna, vermutería |
| bar_chair_round_01 | Taburete de bar tapizado | Bar, cafetería (atrezzo de escenas) |
| WoodenTable_02 | Mesa de madera | Superficie para componer escenas |
| potted_plant_01 | Planta de interior en maceta | Floristería, decoración, wellness |
| Camera_01 | Cámara réflex vintage | Fotografía, audiovisual |
| classic_laptop | Portátil clásico | Agencia, software, academia |
| book_encyclopedia_set_01 | Juego de enciclopedias | Librería, editorial, abogados |
| BarberShopChair_01 | Sillón de barbero antiguo | Barbería, peluquería |
| CashRegister_01 | Caja registradora antigua (cajón separado) | Tienda, retail con encanto |

En `entornos/`:

| Archivo | Qué es | Para qué |
|---|---|---|
| brown_photostudio_02_1k.hdr | HDRI de estudio fotográfico | `scene.environment`: iluminación realista de producto |
| warm_restaurant_night_1k.hdr | HDRI de restaurante de noche | Ambiente cálido para escenas de hostelería (también de fondo desenfocado) |
| comfy_cafe_360.jpg | Foto 360° real de una cafetería | El visor panorama-360-interior.html |

Para AÑADIR un modelo nuevo: buscar en https://polyhaven.com/models, y
descargar el glTF 1k con la misma estructura de carpeta (la API es
`https://api.polyhaven.com/files/<slug>` → `gltf.1k.gltf` + sus `include`).
Después, darlo de alta en el catálogo de `../cargador-3d.js`.
