# dux

## Ejecución local

### 1. Preparar entorno Python

```bash
cd /ruta/al/dux-reports

pyenv local 3.11.9
python -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

El proyecto importa el paquete como `dux`. Si tu carpeta local no se llama
`dux`, exporta el directorio padre como `PYTHONPATH` antes de ejecutar comandos:

```bash
export PYTHONPATH="$(dirname "$PWD")"
```

### 2. Configurar variables locales

Para ejecutar la aplicación contra el snapshot local, crea un archivo `.env` en
la raíz del repo:

```env
SECRET_KEY=dev-local-secret
SESSION_COOKIE_SECURE=false
SQLALCHEMY_DATABASE_URI=mysql+pymysql://root:root@127.0.0.1:3306/db_dux_wellness_test
```

No subas `.env` al repositorio.

### 3. Subir MySQL local con Docker

```bash
docker run --name dux-mysql \
  -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=db_dux \
  -p 3306:3306 \
  -d mysql:8.0
```

Si el contenedor ya existe:

```bash
docker start dux-mysql
```

Verifica que MySQL esté listo:

```bash
mysqladmin ping -h 127.0.0.1 -P 3306 -u root -proot
```

### 4. Crear snapshot local de Wellness

El script `scripts/create_local_wellness_snapshot.py` crea una base local
reducida para trabajar la migración de Wellness. Lee desde la base configurada
en `SQLALCHEMY_DATABASE_URI` y escribe en una base local.

Para crear el snapshot, `SQLALCHEMY_DATABASE_URI` debe apuntar a la base remota
de lectura, no a la base local. Si tu `.env` ya apunta al snapshot local,
sobrescribe la variable solo para este comando:

```bash
source .venv/bin/activate
export PYTHONPATH="$(dirname "$PWD")"

SQLALCHEMY_DATABASE_URI='mysql+pymysql://USUARIO_READONLY:PASSWORD_URL_ENCODED@HOST:3306/db_dux' \
python scripts/create_local_wellness_snapshot.py \
  --target-uri mysql+pymysql://root:root@127.0.0.1:3306/db_dux_wellness_test \
  --days 90 \
  --plantel 1FF \
  --match-limit 300 \
  --keep-player-names
```

Importante:

- `SQLALCHEMY_DATABASE_URI` = origen remoto de lectura.
- `--target-uri` = destino local que el script puede borrar y recrear.
- No uses la misma base como origen y destino.

El snapshot incluye:

- tablas de autenticación mínimas
- catálogos necesarios para Wellness
- tablas auxiliares mínimas para que la navegación de dashboards existentes no falle por tablas ausentes
- registros Wellness/RPE del período indicado
- una muestra acotada de jornadas, actas y sustituciones para validar dashboards existentes
- observaciones vacías por defecto

El flag `--keep-player-names` mantiene los nombres reales de futbolistas para
que los dashboards existentes que cruzan datos por nombre sigan mostrando
gráficos. Si necesitas anonimizar futbolistas, omite ese flag.

El snapshot está pensado para validar la migración de Wellness y detectar
regresiones básicas en dashboards existentes sin copiar todo el banco remoto.

Usuario local generado:

```text
email: admin@example.com
password: admin123
```

Ese usuario queda activo y asociado al rol ADMIN del snapshot local.

Después de crear el snapshot, actualiza `.env` para apuntar a la base local:

```env
SQLALCHEMY_DATABASE_URI=mysql+pymysql://root:root@127.0.0.1:3306/db_dux_wellness_test
```

### 5. Ejecutar la aplicación

Desde la raíz del repo:

```bash
source .venv/bin/activate
export PYTHONPATH="$(dirname "$PWD")"

python - <<'PY'
from dux import create_app
from dux.models import Base

app = create_app()

with app.app_context():
    User = getattr(Base.classes, "users", None)
    print("users:", User)
    if User is None:
        raise RuntimeError("Tabla users no fue reflejada")

app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
PY
```

Abre:

```text
http://127.0.0.1:5001/auth/login
```

Credenciales locales:

```text
email: admin@example.com
password: admin123
```

### 6. Ejecutar tests

```bash
source .venv/bin/activate
export PYTHONPATH="$(dirname "$PWD")"

python -m unittest discover -s tests
```

## Migraciones de base de datos (Alembic)

El proyecto parte de la revisión **baseline_20250708**.  Todas las migraciones
futuras deben colgar de esta para mantener una historia lineal y evitar
conflictos.

Pasos recomendados:

1. Crea tu rama de trabajo y asegúrate de tener la base actualizada:

   ```bash
   git checkout -b feature/mi-cambio
   git pull origin main
   ```

2. Genera la migración con autogeneración de metadatos ya reflejados:

   ```bash
   alembic revision --autogenerate -m "<descripción>"
   ```

   *No uses* `--head` ni cambies `down_revision`; Alembic ajustará la cadena
   automáticamente al último commit de la rama principal de migraciones.

3. Revisa y ajusta el script generado antes de aplicarlo.

4. Aplica la migración localmente para verificar:

   ```bash
   alembic upgrade head
   ```

5. Sube la rama y abre un *pull-request*.  La CI aplicará `alembic upgrade head`
   contra una base de pruebas para asegurar su validez.

Resumiendo: **nunca reescribas migraciones ya compartidas**.  Si necesitas
corregir algo, genera una nueva migración que modifique lo necesario.
