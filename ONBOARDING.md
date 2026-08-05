# Incorporar una aplicación a la plataforma

Procedimiento para desplegar una aplicación **nueva** en una plataforma que **ya existe**.

Se escribe con marcadores `<app>`, `<servicio>` y `<entorno>`: sirve igual la primera vez que
la décima.

**Cuándo se aplica**: un equipo de desarrollo lleva meses en su propio repositorio, termina su
MVP y pide desplegarlo en `dev` para seguir probando. A partir de ahí, todo se automatiza.

**Qué se asume ya montado**: ArgoCD instalado, `bootstrap/root.yaml` aplicado, el controlador
de secretos sellados corriendo, y el flujo de promoción operativo. Si eso no existe todavía,
ver `RUNBOOK.md` §9.

---

## Fase 1 — Reunión de traspaso

No empieces a escribir YAML hasta tener estas respuestas. Cada una determina una decisión
concreta, y dos de ellas pueden hacer que la aplicación **no sea desplegable** sin cambios de
código.

| # | Pregunta | Qué determina |
|---|---|---|
| 1 | ¿Cuántos procesos distintos son? | Un Deployment por cada uno |
| 2 | ¿Qué puerto escucha cada uno? | Services, probes y `containerPort` |
| 3 | ¿Cuál se expone al exterior? | Quién recibe el Ingress; el resto son ClusterIP |
| 4 | ¿Hay endpoint de salud? ¿Alguno comprueba dependencias? | `livenessProbe` vs `readinessProbe` |
| 5 | ¿Qué variables de entorno necesita? ¿Cuáles son secretos? | `values` vs `SealedSecret` |
| 6 | ¿Qué dependencias externas usa? (BD, cache, colas) | Qué vive dentro y qué fuera del clúster |
| 7 | ¿Cuánta CPU y memoria consume en reposo y bajo carga? | `requests` y `limits` |
| 8 | ¿Alguno guarda estado en disco? | `Deployment` o `StatefulSet` con PVC |
| 9 | **¿Aguanta varias réplicas a la vez?** | Si se puede escalar y hacer rolling update |
| 10 | **¿Qué hace al recibir SIGTERM?** | Si un despliegue pierde peticiones |
| 11 | ¿Corre como root? ¿Lo necesita? | `securityContext` |
| 12 | ¿Cómo se crea o migra el esquema de la base de datos? | Job de migración o al arrancar |
| 13 | ¿Quién aprueba los despliegues a producción? | `CODEOWNERS` |

### Las dos que evitan más incidentes

**Pregunta 9 — varias réplicas.** Si la aplicación asume ser única (escribe en un fichero
local, consume una cola sin coordinarse, guarda sesión en memoria), **no se puede escalar ni
hacer rolling update**: durante un despliegue conviven dos réplicas por definición. La
respuesta "no lo hemos probado" equivale a "no".

Si la respuesta es no, hay dos salidas: arreglarlo en el código, o desplegar con
`strategy: Recreate` y una sola réplica, asumiendo caída en cada despliegue. Esa segunda
opción se decide con el equipo, no por tu cuenta.

**Pregunta 10 — SIGTERM.** Kubernetes envía SIGTERM y espera 30 segundos antes de matar el
proceso. Una aplicación que muere de golpe corta las peticiones en vuelo, y eso se nota como
errores 502 intermitentes cada vez que se despliega. Lo que quieres oír: *deja de aceptar
conexiones nuevas, termina las que tiene, y sale*.

### Preguntas de contexto que también conviene hacer

- ¿Cuántas peticiones por segundo espera? → dimensionar réplicas
- ¿Hay picos previsibles? → HPA sí o no
- ¿Puede quedarse sin su base de datos unos segundos? → comportamiento de readiness
- ¿Qué significa "caído" para vosotros? → qué vigilar

---

## Fase 2 — Requisitos mínimos de admisión

Comprobar antes de aceptar el traspaso. Si algo falta, se devuelve al equipo **con el motivo**,
no con una opinión.

| Requisito | Si falta, esto es lo que pasa |
|---|---|
| Configuración por variables de entorno | Hace falta una imagen distinta por entorno, y deja de poderse promover el mismo artefacto |
| Endpoint de liveness que **no** toque dependencias | Si la base de datos cae, Kubernetes reinicia todos los pods en bucle y empeora la caída |
| Endpoint de readiness que **sí** compruebe dependencias | Llega tráfico a pods que no pueden atenderlo |
| Logs a stdout/stderr | `kubectl logs` no muestra nada; hay que entrar al contenedor |
| Imagen que no corra como root | Un contenedor comprometido arranca con más privilegios de los necesarios |
| Sin estado en el disco local del contenedor | Se pierde en cada reinicio, y los reinicios son normales |
| Apagado ordenado ante SIGTERM | Peticiones cortadas en cada despliegue |
| `Dockerfile` en el repositorio | No hay nada que construir de forma reproducible |

---

## Fase 3 — Preparar el repositorio del equipo

Los tres workflows entran en **su** repositorio, no en este:

| Workflow | Dispara con | Qué hace |
|---|---|---|
| `ci.yml` | push y pull request | Lint y tests. No despliega nada |
| `release.yml` | publicar un Release | Test → construir y publicar imágenes. **No despliega** |
| `promote.yml` | manual (`workflow_dispatch`) | Abre un Pull Request contra este repositorio |

Ningún workflow del equipo escribe en `main` de este repositorio. `release.yml` solo publica
imágenes; a `dev` llega por un pull request que abre Renovate desde aquí y que se mergea solo
en cuanto `Validate` pasa, y a staging y producción por el pull request de `promote.yml`, que
sí necesita una persona. En los tres casos, **el merge es el despliegue**.

### Protección de `main` en el repositorio del equipo

La otra mitad del ciclo: el cambio de código entra por rama y pull request, nadie hace push a
`main`. Se configura en **su** repositorio (Settings → Rules → Rulesets, target `main`):

| Ajuste | Valor |
|---|---|
| Require a pull request before merging | sí, 1 aprobación |
| Require status checks to pass | **CI** (`ci.yml`) |
| Dismiss stale approvals when new commits are pushed | sí |
| Block force pushes | sí |
| Bypass list | vacía |

El release se corta de `main` ya revisado. `release.yml` hace checkout del tag del Release y
vuelve a pasar los tests sobre ese commit exacto antes de construir nada — así la imagen que
acaba en producción es siempre código que pasó por revisión **y** por CI, aunque alguien corte
un release desde un tag antiguo donde nadie garantiza que CI llegara a ejecutarse.

Y sus secrets:

```powershell
gh secret set DOCKERHUB_USERNAME -R <owner>/<repo-de-la-app> --body "<usuario>"
gh secret set DOCKERHUB_TOKEN    -R <owner>/<repo-de-la-app>
gh secret set CONFIG_REPO_TOKEN  -R <owner>/<repo-de-la-app>
```

`CONFIG_REPO_TOKEN` lo usa **solo `promote.yml`**. Es un token fine-grained acotado a este
repositorio de plataforma, con `Contents: Read and write` y `Pull requests: Read and write`.
Nunca con acceso al repositorio de la propia aplicación: un runner comprometido no debe poder
reescribir el workflow que lo ejecuta.

`Contents: write` suena a mucho, pero con la protección de rama de RUNBOOK §11 **no permite
escribir en `main`**: solo crear la rama del pull request.

**Este pipeline no tiene kubeconfig ni credenciales del clúster.** Su privilegio máximo es
proponer un cambio de YAML en un repositorio de configuración.

---

## Fase 4 — Primer release: que existan las imágenes

```powershell
gh release create v1.0.0 -R <owner>/<repo-de-la-app> --title "v1.0.0" --notes "..."
gh run watch -R <owner>/<repo-de-la-app>
```

El pipeline construye y publica las imágenes, y ahí termina: no toca este repositorio. Por eso
el primer release funciona aunque `apps/<app>/` todavía no exista. Cuando exista, Renovate
empezará a proponer los tags nuevos por pull request.

Y **hacer públicas las imágenes** en el registro, o el clúster no podrá descargarlas. Docker
Hub crea los repositorios privados por defecto en el primer push:

```powershell
curl.exe -s "https://hub.docker.com/v2/repositories/<usuario>/<imagen>/tags?page_size=5"
```

Un 404 significa que sigue privado.

> El síntoma de saltarse esto es `ImagePullBackOff` con `pull access denied`, que se parece
> mucho a "la imagen no existe" pero tiene otra causa. Los registros privados devuelven
> "no encontrado" ante peticiones anónimas a propósito, para no revelar qué imágenes existen.

---

## Fase 5 — Preparar el clúster

Lo que no se puede describir en Git, o que conviene tener antes del primer sync:

```powershell
kubectl create namespace <app>-dev
kubectl create namespace <app>-staging
kubectl create namespace <app>-prod
```

Y las entradas DNS de los hosts del Ingress (en local, el archivo `hosts`; en un clúster real,
registros en el DNS corporativo).

Los namespaces también los crea el chart; crearlos antes solo evita que los pods arranquen en
`CreateContainerConfigError` esperando un secreto que aún no existe.

---

## Fase 6 — Crear `apps/<app>/`

**No se escribe ningún chart.** `charts/app` renderiza cualquier aplicación a partir de su
lista de componentes, así que dar de alta una es escribir ficheros de valores:

```
apps/<app>/
├── values.yaml              # la forma de la aplicación
└── envs/
    ├── dev.yaml
    ├── staging.yaml
    └── prod.yaml
```

Más un `AppProject` en `platform/projects/<app>.yaml` — copiar el de una aplicación existente
y ajustar el nombre, el glob de namespaces y la lista de tipos de recurso que la aplicación
usa realmente. Es lo que impide que un error en esta aplicación toque los namespaces de otra.

No hay que crear ninguna `Application`: el `ApplicationSet` de
`platform/applicationsets/apps.yaml` recorre `apps/*/envs/*.yaml` y genera una por fichero.

### La regla que gobierna el reparto

**`values.yaml` describe la FORMA de la aplicación. `envs/` describe ESTE entorno.**

A `values.yaml`: qué componentes existen, sus puertos, sus probes, si usan base de datos o
Redis, sus variables de entorno, las rutas del Ingress y el repositorio de cada imagen.

A `envs/`: namespace, host del Ingress, nombre de la base de datos, tags de las imágenes,
réplicas, recursos, HPA, y el secreto cifrado.

Nunca un fichero de valores por entorno que duplique la forma: divergen, y el fallo aparece
solo en producción.

### Cómo se declara un componente

```yaml
components:
  <nombre>:                     # el recurso será <app>-<nombre>
    image: { repository: <owner>/<imagen>, tag: "", pullPolicy: IfNotPresent }
    replicaCount: 1
    port: 8000                  # containerPort
    service: { port: 8000 }
    database: true              # inyecta DB_HOST/PORT/USER/NAME/PASSWORD
    redis: false                # inyecta REDIS_HOST/REDIS_PORT
    env: []                     # variables extra; pasan por `tpl`, así que
                                # pueden referenciar a otro componente por nombre
    probes:                     # por defecto /health y /ready
      liveness:  { path: /health, initialDelaySeconds: 10, periodSeconds: 15 }
      readiness: { path: /ready,  initialDelaySeconds: 5,  periodSeconds: 10 }
```

`tag: ""` es deliberado: la plantilla usa `required`, así que un tag sin poner rompe el
`helm template` del pull request en vez de desplegar algo indefinido.

Solo los componentes listados en `ingress.paths` son alcanzables desde fuera. El resto son
ClusterIP y solo existen para el resto de la aplicación.

### Cuatro valores que deben ser únicos o las apps chocan

`namespace.name`, `app`, `ingress.host` y los `components.*.image.repository`. Dos
aplicaciones que rendericen un Service con el mismo nombre en el mismo namespace se pisan, y
con `selfHeal: true` entran en un bucle de sobrescritura mutua.

### El secreto

Una vez existan `apps/<app>/values.yaml` (con `database.passwordSecret.name`) y el fichero de
entorno (con `namespace.name`):

```powershell
python scripts\secrets.py rotate <app> <entorno>
```

Pide la contraseña sin eco, la sella para ese namespace y la escribe en el fichero de entorno.
**Uno por entorno**: el cifrado está atado a namespace + nombre y no se puede reutilizar.

```powershell
python scripts\secrets.py list      # comprobar que aparecen todos
python scripts\secrets.py check     # y que el cluster puede descifrarlos
```

### Validar antes de commitear

```powershell
helm lint charts\app -f apps\<app>\values.yaml -f apps\<app>\envs\dev.yaml
helm template <app>-dev charts\app -f apps\<app>\values.yaml -f apps\<app>\envs\dev.yaml
```

Repetir con cada entorno. Es lo mismo que hará `validate.yml` en el pull request, y descubrir
un error aquí cuesta segundos.

---

## Fase 7 — El onboarding

```powershell
git pull --rebase
git checkout -b onboard/<app>
git add apps/<app> platform/projects/<app>.yaml
git status
git commit -m "feat: onboard <app> in dev and staging"
git push origin onboard/<app>
gh pr create -R morbanjunior/platform-gitops --fill
```

Un pull request, como cualquier otro cambio: `main` no acepta push directo (ver RUNBOOK §11).
Con `Validate` en verde y la aprobación, se mergea.

**Eso es todo.** No hay `kubectl apply`, no se toca `bootstrap/root.yaml`, no se toca ninguna
otra aplicación. El `ApplicationSet` descubre los entornos nuevos recorriendo
`apps/*/envs/*.yaml` y genera sus `Application`.

> `git pull --rebase` no es cortesía: en un repositorio GitOps escriben también Renovate y los
> merges de promoción. Tú no eres el único autor.

---

## Fase 8 — Verificación

```powershell
kubectl get application -n argocd
kubectl get pods,statefulset,pvc,ingress -n <app>-dev
kubectl get sealedsecret -n <app>-dev
```

Y una petición real por el Ingress, no solo `kubectl get pods`: que los pods estén `Running`
no prueba que la aplicación responda.

```powershell
curl.exe -s http://dev.<app>.local/<endpoint-de-salud>
```

---

## Después del onboarding: quién toca qué

| | Equipo de desarrollo | Equipo DevOps | El pipeline |
|---|---|---|---|
| Código de la aplicación | ✅ | | |
| Publicar una versión | ✅ | ✅ | |
| **Versión desplegada** | | | ✅ (solo esto) |
| Forma de la app: réplicas, recursos, HPA, entornos | | ✅ | |
| Aprobar producción | | ✅ CODEOWNER | |

**El equipo de desarrollo no vuelve a tocar este repositorio.** Su flujo es: push (corre CI) y
release cuando quieran desplegar a dev.

**Tú vuelves a tocarlo solo cuando cambia la forma de la aplicación**: un servicio nuevo, una
variable nueva, más réplicas, otro entorno. Nunca para cambiar una versión — eso es del
pipeline.

---

## Problemas del día uno

| Síntoma | Causa |
|---|---|
| `ImagePullBackOff` con `not found` | El tag no existe todavía en el registro |
| `ImagePullBackOff` con `pull access denied` | El repositorio del registro es privado |
| `CreateContainerConfigError` | Falta el Secret en ese namespace |
| `Running` pero `0/1` | La readiness probe falla: no alcanza sus dependencias |
| La SealedSecret no crea su Secret | Ya existe uno creado a mano. Ver `RUNBOOK.md` §8 |
| El Application no aparece | El fichero no encaja con `apps/*/envs/*.yaml`, o le falta `environment:` / `namespace.name` |
| El Application queda en `Unknown` con error de proyecto | Falta `platform/projects/<app>.yaml`, o su nombre no coincide con la carpeta |
| `SyncError: not permitted in project` | El `AppProject` no lista ese namespace o ese tipo de recurso |
| `git push` de promoción falla porque la rama existe | Ya hay un PR abierto para esa versión y entorno. Mergearlo, no reintentar |
| `gh pr create` falla con 403 | Al token le falta `Pull requests: write` |

---

## Checklist de aceptación

- [ ] Las respuestas del cuestionario están escritas en algún sitio localizable
- [ ] La aplicación cumple los requisitos mínimos de la fase 2
- [ ] Los tres workflows están en el repositorio del equipo y su CI está en verde
- [ ] Las imágenes de la primera versión existen y son accesibles desde el clúster
- [ ] `apps/<app>/` valida en local para **todos** los entornos
- [ ] `platform/projects/<app>.yaml` existe y lista solo lo que la aplicación usa
- [ ] Los secretos están sellados, uno por entorno
- [ ] Las Applications aparecen `Synced/Healthy`
- [ ] Una petición real por el Ingress responde
- [ ] Una promoción de prueba a staging abre su pull request
- [ ] El equipo sabe cómo desplegar y a quién pedir una promoción a producción
