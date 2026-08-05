# Runbook de la plataforma

Manual de operación. Qué se ejecuta, cuándo y qué hacer cuando algo falla.

Regla que gobierna todo lo demás: **el clúster refleja este repositorio**. No se
despliega con `kubectl` ni con `helm` — se cambia un archivo y ArgoCD reconcilia.
`kubectl` se usa para *mirar*, no para *cambiar*.

---

## 1. Ver el estado

```powershell
# Qué revisión de Git tiene aplicada cada entorno
kubectl get application -n argocd -o custom-columns="NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,REV:.status.sync.revision"

# Qué imagen corre realmente en cada entorno (la verdad definitiva)
kubectl get deploy -A -o jsonpath="{range .items[*]}{.metadata.namespace}{'\t'}{.metadata.name}{'\t'}{.spec.template.spec.containers[0].image}{'\n'}{end}"

# Qué versión DEBERÍA correr según Git
Select-String -Path apps\*\envs\*.yaml -Pattern "tag:"

# Pods de un entorno
kubectl get pods,hpa,ingress -n tasks-prod
```

Si la imagen desplegada y la de Git no coinciden, ArgoCD todavía no ha
sincronizado (hasta 3 minutos) o el sync falló — ver §6.

### Estados de una Application

| Sync | Health | Significa |
|---|---|---|
| `Synced` | `Healthy` | Todo correcto |
| `OutOfSync` | cualquiera | Git cambió, aún no aplicado. Normal unos segundos |
| `Synced` | `Progressing` | Aplicado, los pods aún arrancan |
| `Synced` | `Degraded` | Aplicado pero algo no arranca. Ver §6 |
| `Unknown` | | ArgoCD no puede leer el repo. Ver §6 |

---

## 2. Desplegar una versión nueva a dev (no requiere intervención)

El cambio de código entra en `main` por pull request revisado — `main` del repo
de la aplicación tampoco acepta push directo. Después, en el **repo de código**,
no aquí:

```powershell
gh release create v1.0.5 -R morbanjunior/api-backend --title "v1.0.5" --notes "..."
gh run watch -R morbanjunior/api-backend
```

Esto ejecuta `test` → `build-and-push` y **ahí se detiene**: publica las
imágenes y no toca este repositorio. El pipeline ya no tiene permiso para
escribir aquí.

A partir de ahí **dev se actualiza solo**. Renovate corre cada hora en este
repositorio, abre un pull request sobre `apps/<app>/envs/dev.yaml` y GitHub lo
mergea en cuanto `Validate` pasa. Nadie tiene que aprobar nada.

```powershell
# No esperar a la hora en punto
gh workflow run renovate.yml -R morbanjunior/platform-gitops

# Ver qué pasó
gh pr list -R morbanjunior/platform-gitops --label deploy --state merged --limit 5
```

Staging y producción no se enteran: solo cambian por promoción (§3).

**Si el PR de dev sigue abierto**, es que `Validate` está en rojo. Eso es el
sistema funcionando: dev se queda en la versión anterior en vez de desplegar
manifiestos que el apiserver rechazaría.

```powershell
gh pr list -R morbanjunior/platform-gitops --label deploy --state open
gh pr checks <n> -R morbanjunior/platform-gitops
```

> Por qué dev no tiene revisor humano y staging sí: la revisión de dev no
> contesta ninguna pregunta que la del repo de código no haya contestado ya. En
> staging y producción sí hay una pregunta nueva — *¿debe esta versión correr
> aquí, ahora?* — y ahí la puerta se queda. Lo que **no** cambia en ningún
> entorno: nada entra en `main` sin pull request. Renovate no hace push; propone
> y la política aprueba (§11).

---

## 3. Promover a staging o producción

Las dos aplicaciones tienen los tres entornos y el mismo workflow. Se promueve
siempre desde el entorno de abajo, y **nunca se reconstruye la imagen**: se
mueve el mismo artefacto que ya lleva tiempo corriendo.

```powershell
# tasks
gh workflow run promote.yml -R morbanjunior/api-backend `
  -f version=v1.0.5 -f environment=staging

# orders
gh workflow run promote.yml -R morbanjunior/orders-platform `
  -f version=v1.0.1 -f environment=prod
```

Esto **no despliega**: abre un Pull Request contra este repositorio.

```powershell
gh pr list -R morbanjunior/platform-gitops
gh pr view <n> -R morbanjunior/platform-gitops --web
```

Antes de mergear, comprobar:

- [ ] El check **Validate** está en verde
- [ ] El diff son exactamente las líneas de `tag:` y nada más
- [ ] La versión lleva tiempo funcionando en el entorno anterior

```powershell
gh pr merge <n> -R morbanjunior/platform-gitops --merge --delete-branch
```

**El merge es el despliegue.** Producción exige además la aprobación de un
CODEOWNER.

---

## 4. Rollback

No hay comando de rollback. Se revierte el commit que desplegó:

```powershell
git pull --rebase
git log --oneline -10                 # localizar el commit de promoción
git revert <sha>
git push
```

ArgoCD devuelve el entorno a la versión anterior en el siguiente poll.

**Urgente** (producción caída): sigue siendo un pull request — `main` no acepta
push directo de nadie, y esa es exactamente la protección que no se quiere
desactivar en medio de un incidente. Lo que se acorta es el ciclo, no el
control:

```powershell
git checkout -b rollback/tasks-prod
git revert <sha>
git push origin rollback/tasks-prod
gh pr create -R morbanjunior/platform-gitops --fill
# Un revisor aprueba; Validate tarda ~1 minuto
gh pr merge --merge --delete-branch
```

Un `git revert` es el diff más fácil de aprobar que existe: devuelve el fichero
a un estado que ya estuvo en producción. Si no hay ningún revisor disponible y
el servicio está caído, el propietario del repositorio puede desactivar
temporalmente la regla de rama — decisión consciente, registrada en el audit log
de GitHub, y que se vuelve a activar en cuanto pasa el incidente.

> Lo que **no** se hace nunca: `kubectl set image` o `kubectl edit`. Con
> `selfHeal: true` ArgoCD lo revierte en segundos y habrás perdido el tiempo
> mientras el servicio seguía caído.

---

## 5. Forzar a ArgoCD (no esperar los 3 minutos)

ArgoCD consulta el repositorio cada **180 segundos**. Tras un merge, hasta 3
minutos de espera es lo normal, no un fallo.

Señal inequívoca de que está en ello: la Application dice `Synced` pero
`.status.sync.revision` **no es el último commit de `main`**. Está sincronizada
con una foto vieja del repositorio.

```powershell
# Qué revisión tiene ArgoCD frente a la que hay en GitHub
kubectl get application <app> -n argocd -o jsonpath="{.status.sync.revision}"
git ls-remote https://github.com/morbanjunior/platform-gitops.git main
```

### Forzar el refresco de una aplicación

```powershell
kubectl annotate application <app> -n argocd argocd.argoproj.io/refresh=hard --overwrite
kubectl get application <app> -n argocd -w
```

Debe pasar por `OutOfSync → Progressing → Synced` con la revisión nueva.

| Valor | Qué hace |
|---|---|
| `refresh=normal` | Vuelve a leer Git y compara con el clúster |
| `refresh=hard` | Igual, pero además descarta la caché del manifiesto renderizado |

Usa `hard` cuando el cambio esté en el chart o en un values file — que es
siempre en esta plataforma, porque las imágenes se despliegan por tag.

### Forzar todas a la vez

```powershell
kubectl annotate application --all -n argocd argocd.argoproj.io/refresh=hard --overwrite
```

### Con la CLI de ArgoCD (si está instalada)

```powershell
argocd app get <app> --refresh          # refresca y muestra el estado
argocd app sync <app>                   # además fuerza la sincronización
argocd app sync <app> --prune
```

### Bajar el intervalo para todo el clúster

Útil mientras se prueban cosas; en producción se usa un webhook en su lugar.

```powershell
kubectl patch configmap argocd-cm -n argocd --type merge -p '{\"data\":{\"timeout.reconciliation\":\"30s\"}}'
kubectl rollout restart statefulset argocd-application-controller -n argocd
```

Para volver al valor por defecto, pon `180s`.

> **Lo correcto en producción es un webhook**: GitHub hace un `POST` a
> `https://<argocd>/api/webhook` en el instante del push y la sincronización
> ocurre en segundos. Aquí no se usa porque el clúster local no es alcanzable
> desde internet, así que el poll es la alternativa razonable. Bajarlo
> indefinidamente no es gratis: cada ciclo es un `git fetch` por Application, y
> con muchas aplicaciones eso se nota en el repo-server y en la API de GitHub.

---

## 6. Secretos sellados

Las contraseñas viven cifradas en `apps/<app>/envs/<entorno>.yaml`. El
controlador `sealed-secrets` (en `kube-system`) las descifra y crea el `Secret`
que consumen los pods.

```powershell
kubectl get sealedsecret -A
kubectl get pods -n kube-system -l name=sealed-secrets-controller
```

Todo el mantenimiento se hace con `scripts/secrets.py`. Requiere `kubeseal` en el
PATH y acceso al clúster:

```powershell
pip install "ruamel.yaml==0.18.10"
python scripts\secrets.py list      # que gestiona la plataforma
python scripts\secrets.py check     # los valores siguen siendo descifrables?
```

`check` sale con código distinto de cero si algún valor no se puede descifrar.
Ejecútalo tras restaurar un respaldo, tras reconstruir un clúster, o de vez en
cuando. Responde a *"¿siguen válidos mis secretos?"* antes de que lo responda un
pod con `CreateContainerConfigError`.

### Rotar una contraseña

Cuando cambia el **valor**:

```powershell
python scripts\secrets.py rotate tasks prod
```

Pide la contraseña sin eco (no queda en el historial de PowerShell), la sella
para ese namespace y la escribe en el fichero de entorno. Con `--all-envs` lo
hace para todos los entornos de la aplicación.

Después:

1. **Cambiar la contraseña en la base de datos** — el script no lo hace
2. Commit y push (pull request para producción)
3. `kubectl rollout restart deployment -n tasks-prod`

Ese tercer paso hace falta porque `secretKeyRef` se lee **al arrancar el
contenedor**: un pod vivo conserva el valor viejo aunque el Secret ya haya
cambiado.

### Rotar la clave de sellado

Cuando cambia la **clave**, no la contraseña:

```powershell
python scripts\secrets.py reseal            # todos
python scripts\secrets.py reseal tasks prod # uno
```

Usa `kubeseal --re-encrypt`: el controlador descifra con la clave con la que se
selló y vuelve a cifrar con la actual. **No hace falta conocer ninguna
contraseña**, y el script nunca la ve.

Cuándo ejecutarlo: el controlador **rota su clave cada 30 días**. Conserva las
viejas para descifrar, así que nada se rompe de inmediato — pero los valores de
este repositorio siguen atados a claves cada vez más antiguas, y perder una (un
respaldo incompleto basta) los deja irrecuperables. Resellar de vez en cuando
mantiene todo sobre la clave actual.

Los pods no se enteran: el Secret resultante es idéntico.

### Recuperar una contraseña olvidada

Con el respaldo de la clave privada, sin necesidad del clúster original:

```powershell
python scripts\secrets.py recover tasks prod --key <ruta>\sealed-secrets-master.key
```

Imprime el valor en texto plano en la terminal. Úsalo solo cuando de verdad
haga falta.

### Añadir el secreto de una aplicación nueva

```powershell
python scripts\secrets.py rotate <app> <entorno>
```

El script descubre las aplicaciones recorriendo `apps/*/envs/*.yaml`, así que
una aplicación nueva aparece sola en cuanto existe su carpeta. Lee el nombre y
la clave del Secret de `apps/<app>/values.yaml` y el namespace del fichero de
entorno.

**El texto cifrado está atado a namespace + nombre.** Reutilizar el de otro
entorno no funciona: el controlador lo rechaza. Es deliberado — impide que un
valor de desarrollo llegue a producción por copiar y pegar.

### La clave privada

Es lo único de la plataforma que no está en Git. Respaldo:

```powershell
kubectl get secret -n kube-system -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml `
  > <ruta-fuera-de-los-repos>\sealed-secrets-master.key
```

Restauración en un clúster nuevo, **antes** de aplicar el root:

```powershell
kubectl apply -f <ruta>\sealed-secrets-master.key
kubectl delete pod -n kube-system -l name=sealed-secrets-controller
```

Sin ese archivo, los valores cifrados del repositorio son inservibles en el
clúster nuevo y hay que volver a sellarlos todos con la clave nueva.

---

## 7. Añadir una aplicación nueva

Ya no se escribe un chart. `charts/app` renderiza cualquier aplicación a partir
de su lista de componentes; una aplicación nueva son **ficheros de valores**:

```
apps/<nombre>/
├── values.yaml     app, components (image, port, probes, database, redis, env),
│                   ingress.paths, database.passwordSecret.name
└── envs/           dev.yaml, staging.yaml, prod.yaml
                    environment, namespace.name, ingress.host, database.name,
                    el ciphertext, y los tag: de cada componente
```

Y un `AppProject` en `platform/projects/<nombre>.yaml`, copiando el de una app
existente y ajustando el nombre, el glob de namespace y los tipos de recurso que
la aplicación usa realmente.

No hay que crear ninguna `Application`: el `ApplicationSet` recorre
`apps/*/envs/*.yaml` y genera una por fichero de entorno.

Antes de commitear:

```powershell
helm lint charts\app -f apps\<nombre>\values.yaml -f apps\<nombre>\envs\dev.yaml
helm template <nombre>-dev charts\app -f apps\<nombre>\values.yaml -f apps\<nombre>\envs\dev.yaml
```

Y lo que hay que preparar **fuera** de Git:

```powershell
kubectl create secret generic <nombre>-db-credentials -n <nombre>-dev --from-literal=password=...
```

Después: pull request. Al mergear, el `ApplicationSet` genera los `Application`
nuevos y ArgoCD despliega. **No se ejecuta `kubectl apply`.**

Cuatro valores tienen que ser únicos o las apps chocan entre sí:
`namespace.name`, `app`, `ingress.host` y los `components.*.image.repository`.

---

## 8. Diagnóstico de fallos

### `kubectl` da `connection refused`

Minikube publica el apiserver en un puerto aleatorio del host; al reiniciarse el
contenedor cambia y el kubeconfig queda obsoleto.

```powershell
minikube start
minikube update-context
kubectl get nodes
```

### La aplicación está `Degraded`

```powershell
kubectl get pods -n <namespace>
kubectl describe pod -n <namespace> <pod>
kubectl logs -n <namespace> <pod> --tail=50
```

| Estado del pod | Causa habitual |
|---|---|
| `CreateContainerConfigError` | Falta el `Secret` en ese namespace |
| `ImagePullBackOff` | El tag no existe en Docker Hub, o el repo es privado |
| `Running` pero `0/1` | La readiness probe falla: no alcanza la base de datos |
| `CrashLoopBackOff` | La aplicación cae al arrancar. Mirar los logs |

### `Running 0/1` — la base de datos

```powershell
kubectl exec -n <namespace> deploy/<deploy> -- python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/ready').read())"

docker ps --filter "name=k8s-postgres"
docker exec k8s-postgres psql -U appuser -d appdb -c "\l"
```

Postgres corre **fuera** del clúster; los pods lo alcanzan por
`host.minikube.internal`.

### El pipeline falla con 403

El token fine-grained (`CONFIG_REPO_TOKEN`) necesita, sobre este repositorio:

| Permiso | Para qué |
|---|---|
| Contents: Read and write | `update-dev` hace commit y push |
| Pull requests: Read and write | `promote.yml` abre el PR |

Síntoma característico de que falta el segundo: **la rama aparece en GitHub pero
no hay Pull Request**.

Tras arreglarlo, no hace falta un release nuevo:

```powershell
gh run rerun <run-id> -R morbanjunior/api-backend --failed
```

### Una SealedSecret no crea su Secret

```powershell
kubectl get sealedsecret <nombre> -n <ns> -o jsonpath="{.status.conditions[0].message}"
kubectl logs -n kube-system -l name=sealed-secrets-controller --tail=30
```

**`already exists and is not managed by SealedSecret`**

Ya hay un `Secret` con ese nombre que el controlador no creó — normalmente uno
hecho a mano con `kubectl create secret`. El controlador **no lo sobrescribe a
propósito**: adoptar cualquier Secret existente permitiría a quien pueda
commitear en este repositorio pisar credenciales gestionadas por otro sistema.

Dos salidas:

```powershell
# Simple: borrarlo y dejar que el controlador lo cree
kubectl delete secret <nombre> -n <ns>

# Sin ventana: transferir la propiedad del Secret existente
kubectl annotate secret <nombre> -n <ns> sealedsecrets.bitnami.com/managed=true
```

Borrar el Secret no interrumpe a los pods que ya corren: `secretKeyRef` se lee
al arrancar el contenedor. En una migración de producción con muchos secretos,
usa la anotación.

**`Error updating, giving up`**

El controlador tiene backoff exponencial y **abandona** una clave que falla
siempre. Cuando arreglas la causa, ya no la está mirando. Fuerza un resync
completo:

```powershell
kubectl delete pod -n kube-system -l name=sealed-secrets-controller
```

Es seguro: el controlador no guarda estado, toda la verdad está en los objetos
de la API. Al arrancar vuelve a listar todas las SealedSecret y las procesa.

Comprobación de que el Secret lo creó el controlador y no una persona:

```powershell
kubectl get secret <nombre> -n <ns> -o jsonpath="{.metadata.ownerReferences[0].kind}"
```

Debe decir `SealedSecret`. Vacío significa que sigue siendo el manual.

### ArgoCD no se entera del cambio

Ver **§5. Forzar a ArgoCD**. Comprobación rápida de si es solo el poll:

```powershell
kubectl get application <app> -n argocd -o jsonpath="{.status.sync.revision}"
```

Si esa revisión no es el último commit de `main`, no hay ningún fallo — está
esperando su próximo ciclo.

### El navegador no muestra el cambio

Casi siempre es caché. Comprobar sin navegador:

```powershell
curl.exe -s http://dev.tasks.local/ | Select-String "Tasks v"
```

Si ahí sale la versión nueva, `Ctrl+Shift+R` en el navegador.

---

## 9. Arranque del clúster desde cero

```powershell
minikube start
minikube update-context

kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml --server-side
kubectl wait --for=condition=available --timeout=300s deployment --all -n argocd

# Controlador de secretos sellados
kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.38.4/controller.yaml
kubectl rollout status deployment sealed-secrets-controller -n kube-system

# Restaurar la clave privada ANTES de desplegar nada, o los secretos del
# repositorio no se podrán descifrar
kubectl apply -f <ruta>\sealed-secrets-master.key
kubectl delete pod -n kube-system -l name=sealed-secrets-controller

# El único kubectl apply de la plataforma en sí
kubectl apply -f bootstrap\root.yaml
```

Todo lo demás se reconstruye solo desde este repositorio, **incluidas las
contraseñas**: van cifradas en los ficheros de entorno y el controlador las
descifra al aplicarlas.

**El único estado que no vive en Git es la clave privada del controlador.** Y es
la pieza correcta para dejar fuera: una sola clave maestra en lugar de una
contraseña por entorno y por aplicación.

> Sin ArgoCD ni Sealed Secrets instalados no hay nada que reconstruir: son las
> herramientas que hacen posible el GitOps, no aplicaciones gestionadas por él.
> Gestionar el controlador de secretos con ArgoCD sería un huevo y gallina.

---

## 10. Acceso a la UI de ArgoCD

```powershell
$pw = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}"
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($pw))

kubectl port-forward svc/argocd-server -n argocd 8081:443
```

https://localhost:8081 — usuario `admin`.

Para que los Ingress respondan en `127.0.0.1`, dejar corriendo en una terminal
de Administrador:

```powershell
minikube tunnel
```

---

## 11. Protección de la rama `main`

Esta configuración **no vive en el repositorio** — es ajuste de GitHub, y sin
ella todo lo demás (CODEOWNERS, el flujo de PR, Renovate) es una recomendación,
no un control. Se documenta aquí para que sea reproducible en otra organización
o después de recrear el repositorio.

`main` es la rama que ArgoCD sincroniza. Escribir en ella *es* desplegar.

### Regla de rama (Settings → Rules → Rulesets, target `main`)

| Ajuste | Valor |
|---|---|
| Require a pull request before merging | sí |
| Required approvals | **0** |
| Require review from Code Owners | **sí** |
| Dismiss stale approvals when new commits are pushed | sí |
| Require status checks to pass | sí → **Validate** |
| Block force pushes | sí |
| Restrict deletions | sí |
| **Bypass list** | **solo `@morbanjunior`** (ver abajo) |
| Allow auto-merge (Settings → General) | sí |

Lo que importa es **quién NO está** en la lista de bypass: ningún bot. Ni
Renovate, ni el `CONFIG_REPO_TOKEN` de `promote.yml`. Esa es la diferencia con
el diseño anterior, donde un pipeline escribía en `main` directamente y la
protección solo aplicaba a las personas.

### Por qué el mantenedor sí está en la lista

Por una limitación de GitHub, no por comodidad: **no se puede aprobar el propio
pull request**. Los PR de promoción los abre `promote.yml` con un PAT que es de
`@morbanjunior`, así que figura como autor de un PR que exige la aprobación de
`@morbanjunior`. Sin el bypass, staging y producción serían inmergeables.

La puerta sigue siendo real: el PR existe, `Validate` tiene que estar verde, el
merge es un acto deliberado y cada uso del bypass queda en el audit log de
GitHub. Lo que no hay es una segunda persona, y eso no lo arregla una regla.

> Cuando haya un segundo mantenedor, vaciar la lista y exigir su aprobación es
> un cambio de una casilla. La alternativa sin esperar a nadie es abrir los PR
> de promoción desde una cuenta máquina distinta, para que el autor no sea quien
> aprueba.

### Por qué `Required approvals: 0` no debilita producción

Parece laxo y no lo es. Quien decide si hace falta una persona es
`CODEOWNERS`, ruta por ruta:

| Ruta | Propietario | Qué hace falta para mergear |
|---|---|---|
| `apps/*/envs/dev.yaml` | **ninguno**, deliberadamente | solo que `Validate` pase → auto-merge |
| `apps/*/envs/staging.yaml` | `@morbanjunior` | su aprobación |
| `apps/*/envs/prod.yaml` | `@morbanjunior` | su aprobación |
| `charts/`, `platform/`, `bootstrap/`, `.github/` | `@morbanjunior` | su aprobación |
| cualquier otra cosa | catch-all `*` | su aprobación |

La aprobación genérica que se quita es la que bloqueaba la única ruta que debe
fluir sola. Todo lo demás sigue exigiendo al propietario, y *Require review from
Code Owners* es lo que lo impone.

> Si algún día `dev.yaml` vuelve a tener propietario, los pull requests de
> Renovate dejan de auto-mergearse y se quedan abiertos para siempre. La regla
> sin propietario está al final de `CODEOWNERS` porque ahí gana la última
> coincidencia; moverla arriba la desactiva en silencio.

### Token `RENOVATE_TOKEN`

Fine-grained, con acceso **solo** a `platform-gitops`. Vive como secret en este
mismo repositorio y lo usa `renovate.yml`:

| Permiso | Motivo |
|---|---|
| Contents: Read and write | crear la rama del PR |
| Pull requests: Read and write | abrirlo y marcarlo para auto-merge |
| Issues: Read and write | el dependency dashboard |

**No sirve `secrets.GITHUB_TOKEN`.** GitHub se niega a disparar workflows desde
eventos creados con el token de ambiente — es su protección contra que un
workflow se dispare a sí mismo en bucle. Un PR abierto con él nunca ejecutaría
`Validate`, y como `Validate` es un check obligatorio que nunca reporta, el
auto-merge esperaría indefinidamente. Un token distinto rompe ese bucle.

> Alternativa: instalar la **GitHub App oficial de Renovate** en lugar del
> workflow. Sus PRs vienen de la app, así que disparan `Validate` igual. Se
> gana no gestionar un PAT; se pierde tener la ejecución dentro del repositorio.

### Token `CONFIG_REPO_TOKEN`

Fine-grained, con acceso **solo** a `platform-gitops`. Lo usa `promote.yml` en
los repos de aplicación:

| Permiso | Motivo |
|---|---|
| Contents: Read and write | crear la rama de promoción |
| Pull requests: Read and write | abrir el pull request |

`Contents: write` suena a mucho, pero con la regla de rama activa **no permite
escribir en `main`**: solo crear ramas. El único camino a `main` sigue siendo un
PR aprobado.

Los repos de aplicación ya no necesitan nada más: `release.yml` no toca este
repositorio.

### Verificar que está bien puesto

```powershell
# Debe ser RECHAZADO
git checkout main; git commit --allow-empty -m "test"; git push

# Un PR que toque prod.yaml debe ser RECHAZADO sin la aprobación del CODEOWNER
gh pr merge <n> -R morbanjunior/platform-gitops --merge
```

Si alguno de los dos pasa, la regla no está activa o alguien está en la lista de
bypass.

Y al revés, para comprobar que dev sí fluye: abrir un PR que toque **solo**
`apps/tasks/envs/dev.yaml` y mirar en la UI que GitHub **no** pide revisión de
propietario. Ese es el punto más frágil de toda la configuración y hay que verlo
con los ojos, no deducirlo.

### La otra mitad: `main` de los repos de código

Mismo principio en `api-backend` y `orders-platform`. El cambio entra por rama y
pull request; nadie hace push a `main`:

| Ajuste en `main` | Valor |
|---|---|
| Require a pull request before merging | sí, **1** aprobación |
| Require status checks to pass | **CI** (`ci.yml`) |
| Dismiss stale approvals | sí |
| Block force pushes | sí |
| Bypass list | solo `@morbanjunior`, por el mismo motivo de arriba |

El release se corta de `main` ya revisado, y `release.yml` vuelve a pasar los
tests sobre ese commit exacto antes de construir nada. Así la imagen que acaba
en producción es siempre código que pasó por revisión **y** por CI, aunque el
release se corte de un tag antiguo.

---

## Entornos

**tasks**

| | Namespace | Host | Base de datos | Cómo se actualiza |
|---|---|---|---|---|
| dev | `tasks-dev` | dev.tasks.local | `appdb_dev` | **Automático**: Renovate + auto-merge |
| staging | `tasks-staging` | staging.tasks.local | `appdb_staging` | `promote.yml` → PR + revisión |
| prod | `tasks-prod` | tasks.local | `appdb_prod` | `promote.yml` → PR + CODEOWNER |

**orders**

| | Namespace | Host | Base de datos | Cómo se actualiza |
|---|---|---|---|---|
| dev | `orders-dev` | dev.orders.local | `appdb_dev` | **Automático**: Renovate + auto-merge |
| staging | `orders-staging` | staging.orders.local | `appdb_staging` | `promote.yml` → PR + revisión |
| prod | `orders-prod` | orders.local | `appdb_prod` | `promote.yml` → PR + CODEOWNER |

> `orders-prod` es nuevo. Antes de la primera promoción hay que sellar su
> secreto — `python scripts\secrets.py rotate orders prod` — o los pods se
> quedan en `CreateContainerConfigError` esperando un Secret que no existe.
