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

## 2. Desplegar una versión nueva (llega sola a dev)

En el **repo de código**, no aquí:

```powershell
gh release create v1.0.5 -R morbanjunior/api-backend --title "v1.0.5" --notes "..."
gh run watch -R morbanjunior/api-backend
```

Esto ejecuta `test` → `build-and-push` → `update-dev`. En ≤3 minutos, **dev**
corre la versión nueva. Staging y producción no se enteran.

---

## 3. Promover a staging o producción

```powershell
gh workflow run promote.yml -R morbanjunior/api-backend `
  -f version=v1.0.5 -f environment=staging
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

**Urgente** (producción caída, sin tiempo para el ciclo de PR): editar
`apps/<app>/envs/prod.yaml` a la versión buena, commitear a `main` y push. Saltarse
la revisión es una decisión consciente, no el procedimiento normal — y queda
registrada en el historial igual que cualquier otra.

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

### Rotar una contraseña

```powershell
kubectl create secret generic tasks-db-credentials -n tasks-prod `
  --from-literal=password=LA_NUEVA --dry-run=client -o yaml |
  kubeseal --format yaml --controller-name sealed-secrets-controller --controller-namespace kube-system
```

Copia el valor de `encryptedData.password` a `database.passwordSecret.encrypted`
del fichero de entorno correspondiente, commitea y push. Para producción, por
pull request como cualquier otro cambio.

El controlador actualiza el `Secret` en el siguiente sync, pero **los pods no
recogen el valor nuevo solos**: `secretKeyRef` se lee al arrancar el contenedor.

```powershell
kubectl rollout restart deployment -n tasks-prod
```

### Añadir el secreto de una aplicación nueva

Mismo comando, cambiando namespace y nombre. El nombre tiene que coincidir con
`database.passwordSecret.name` del chart.

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

```
apps/<nombre>/
├── chart/          Chart.yaml, values.yaml (defaults + huecos ""), templates/
├── envs/           dev.yaml, staging.yaml, prod.yaml
└── applications/   dev.yaml, staging.yaml, prod.yaml
```

Antes de commitear:

```powershell
helm lint apps\<nombre>\chart -f apps\<nombre>\chart\values.yaml -f apps\<nombre>\envs\dev.yaml
helm template <nombre> apps\<nombre>\chart -f apps\<nombre>\chart\values.yaml -f apps\<nombre>\envs\dev.yaml
```

Y lo que hay que preparar **fuera** de Git:

```powershell
kubectl create secret generic <nombre>-db-credentials -n <nombre>-dev --from-literal=password=...
```

Después: commit y push. El `root` detecta los `Application` nuevos y despliega.
**No se ejecuta `kubectl apply`.**

Cuatro valores tienen que ser únicos o las apps chocan entre sí:
`namespace.name`, `fullnameOverride`, `ingress.host`, `image.repository`.

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

## Entornos

| | Namespace | Host | Base de datos | Cómo se actualiza |
|---|---|---|---|---|
| dev | `tasks-dev` | dev.tasks.local | `appdb_dev` | Automático en cada release |
| staging | `tasks-staging` | staging.tasks.local | `appdb_staging` | Pull request |
| prod | `tasks-prod` | tasks.local | `appdb_prod` | Pull request + CODEOWNER |
