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
| `release.yml` | publicar un Release | Test → construir imágenes → actualizar `dev` |
| `promote.yml` | manual (`workflow_dispatch`) | Abre un Pull Request contra este repositorio |

Y sus secrets:

```powershell
gh secret set DOCKERHUB_USERNAME -R <owner>/<repo-de-la-app> --body "<usuario>"
gh secret set DOCKERHUB_TOKEN    -R <owner>/<repo-de-la-app>
gh secret set CONFIG_REPO_TOKEN  -R <owner>/<repo-de-la-app>
```

`CONFIG_REPO_TOKEN` es un token fine-grained acotado **solo a este repositorio de
plataforma**, con `Contents: Read and write` y `Pull requests: Read and write`. Nunca con
acceso al repositorio de la propia aplicación: un runner comprometido no debe poder reescribir
el workflow que lo ejecuta.

**Este pipeline no tiene kubeconfig ni credenciales del clúster.** Su privilegio máximo es
escribir YAML y abrir PRs en un repositorio de configuración.

---

## Fase 4 — Primer release: que existan las imágenes

```powershell
gh release create v1.0.0 -R <owner>/<repo-de-la-app> --title "v1.0.0" --notes "..."
gh run watch -R <owner>/<repo-de-la-app>
```

⚠️ **En el primer release, el job `update-dev` va a fallar.** Intenta escribir en
`apps/<app>/envs/dev.yaml`, que aún no existe en este repositorio. Es esperado: los jobs que
importan (`test` y las imágenes) sí completan. Se relanza al terminar la fase 6 con
`gh run rerun --failed`.

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

```
apps/<app>/
├── chart/
│   ├── Chart.yaml
│   ├── values.yaml          # defaults + huecos ""
│   └── templates/
│       ├── _helpers.tpl
│       ├── namespace.yaml
│       ├── sealed-secret.yaml
│       ├── ingress.yaml
│       └── <servicio>.yaml  # Deployment + Service por componente
├── envs/
│   ├── dev.yaml
│   ├── staging.yaml
│   └── prod.yaml
└── applications/
    ├── dev.yaml
    ├── staging.yaml
    └── prod.yaml
```

### La regla que gobierna el reparto

**El chart describe la FORMA de la aplicación. `envs/` describe ESTE entorno.**

Al chart: todo lo que es igual en todos los entornos (qué recursos existen, qué puertos, qué
probes, el nombre del repositorio de las imágenes).

A `envs/`: namespace, host del Ingress, nombre de la base de datos, tags de las imágenes,
réplicas, recursos, HPA, y el secreto cifrado.

Nunca un chart por entorno: divergen, y el fallo aparece solo en producción.

### Cuatro valores que deben ser únicos o las apps chocan

`namespace.name`, `fullnameOverride`, `ingress.host` e `image.repository`. Dos charts que
rendericen un Service con el mismo nombre en el mismo namespace se pisan, y con
`selfHeal: true` entran en un bucle de sobrescritura mutua.

### El secreto

Una vez existan `chart/values.yaml` (con `database.passwordSecret.name`) y el fichero de
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
helm lint apps\<app>\chart -f apps\<app>\chart\values.yaml -f apps\<app>\envs\dev.yaml
helm template <app> apps\<app>\chart -f apps\<app>\chart\values.yaml -f apps\<app>\envs\dev.yaml
```

Repetir con cada entorno. Es lo mismo que hará `validate.yml` en el pull request, y descubrir
un error aquí cuesta segundos.

---

## Fase 7 — El onboarding

```powershell
git pull --rebase
git add apps/<app>
git status
git commit -m "feat: onboard <app> in dev and staging"
git push
```

**Eso es todo.** No hay `kubectl apply`, no se toca `bootstrap/root.yaml`, no se toca ninguna
otra aplicación. El `root` descubre los `Application` nuevos por el patrón
`*/applications/*.yaml`.

> `git pull --rebase` no es cortesía: en un repositorio GitOps escriben también los pipelines
> y los merges de promoción. Tú no eres el único autor.

Después, relanzar el job del primer release:

```powershell
gh run rerun --failed -R <owner>/<repo-de-la-app>
```

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
| El Application no aparece | El fichero no encaja con `apps/*/applications/*.yaml` |
| `update-dev` falla con 403 | Al token le falta acceso a este repositorio |
| `gh pr create` falla con 403 | Al token le falta `Pull requests: write` |

---

## Checklist de aceptación

- [ ] Las respuestas del cuestionario están escritas en algún sitio localizable
- [ ] La aplicación cumple los requisitos mínimos de la fase 2
- [ ] Los tres workflows están en el repositorio del equipo y su CI está en verde
- [ ] Las imágenes de la primera versión existen y son accesibles desde el clúster
- [ ] `apps/<app>/` valida en local para **todos** los entornos
- [ ] Los secretos están sellados, uno por entorno
- [ ] Las Applications aparecen `Synced/Healthy`
- [ ] Una petición real por el Ingress responde
- [ ] Una promoción de prueba a staging abre su pull request
- [ ] El equipo sabe cómo desplegar y a quién pedir una promoción a producción
