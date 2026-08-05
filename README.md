# platform-gitops

Repositorio GitOps de la plataforma. **Lo que está aquí es lo que corre en el
clúster.** ArgoCD reconcilia continuamente: cambiar algo en el clúster a mano
no cambia nada de forma duradera, se revierte solo.

📖 **[ONBOARDING.md](ONBOARDING.md)** — cómo **entra** una aplicación nueva:
qué preguntar al equipo de desarrollo, qué debe cumplir la app, y el
procedimiento del primer despliegue.

🔧 **[RUNBOOK.md](RUNBOOK.md)** — cómo se **opera** a diario: desplegar,
promover, rotar secretos, hacer rollback y diagnosticar fallos.

## Estructura

```
platform-gitops/
├── bootstrap/root.yaml         # kubectl apply UNA vez por clúster
├── platform/                   # AppProjects + ApplicationSet (ver su README)
├── charts/app/                 # UN chart genérico para todas las aplicaciones
└── apps/
    └── <app>/
        ├── values.yaml         # la FORMA de la aplicación: sus componentes
        └── envs/               # dev.yaml, staging.yaml, prod.yaml
```

Cada aplicación es una carpeta de ficheros de valores. No trae plantillas
propias: el chart de `charts/app/` las renderiza a partir de su lista de
componentes. Añadir una app no obliga a tocar las de nadie más, ni a copiar un
chart.

## Cómo se despliega una versión

**Nada llega a `main` sin pasar por un pull request.** Los tres entornos usan el
mismo mecanismo; lo que cambia es quién aprueba.

```
Rama de trabajo → Pull Request en el repo de código
   → revisión → merge a main                                [puerta humana]

Release publicado desde main
   → CI construye y publica las imágenes                    [y ahí se detiene]

dev
   → Renovate detecta el tag nuevo y abre un Pull Request
   → Validate pasa → GitHub lo mergea solo                  [sin intervención]
   → ArgoCD sincroniza dev

staging / prod
   → workflow promote.yml abre un Pull Request
   → un CODEOWNER lo revisa y lo mergea                     [puerta humana]
   → ArgoCD sincroniza ese entorno
```

Ningún pipeline escribe en `main` — tampoco Renovate, que propone y deja que la
política decida. Los repositorios de aplicación no tienen permiso de escritura
sobre este: pueden proponer, no desplegar.

Las puertas humanas están donde equivocarse cuesta algo. En dev el revisor es el
check `Validate`: si el render falla, el pull request se queda abierto y dev
sigue con la versión anterior.

La imagen **nunca se reconstruye** entre entornos: se promueve el mismo
artefacto que ya se validó en el entorno anterior.

## Cómo se hace rollback

`git revert` del commit de promoción. ArgoCD devuelve el clúster a la versión
anterior en el siguiente poll. No hay ningún comando de rollback que aprender:
el historial de git *es* el historial de despliegues.

## Cómo se añade una aplicación nueva

1. Crear `apps/<nombre>/values.yaml` con sus componentes.
2. Crear `apps/<nombre>/envs/<entorno>.yaml` por cada entorno.
3. Pull request.

El `ApplicationSet` de `platform/` recorre `apps/*/envs/*.yaml` y genera las
`Application` que falten. No hay chart que escribir ni `Application` que
mantener en paralelo, y **no se ejecuta `kubectl` en ningún momento.**

## Los secretos sí están aquí, cifrados

Las contraseñas viven en `apps/<app>/envs/<entorno>.yaml` como **SealedSecret**:
cifradas con la clave pública del clúster. Solo el controlador
`sealed-secrets` tiene la clave privada, y esa clave nunca sale del clúster —
por eso el texto cifrado puede estar en un repositorio público.

No confundir con un `Secret` normal, cuyo `data` es **base64**: codificación,
no cifrado. Cualquiera lo descifra con un comando.

Cada bloque está atado a **un namespace y un nombre**. El de `tasks-dev` no se
puede reutilizar en `tasks-prod`, ni aunque la contraseña sea la misma: el
controlador se niega. Eso impide que un valor de desarrollo acabe en producción
por copiar y pegar.

### Lo único que no está en Git

La **clave privada del controlador**, respaldada fuera de los repositorios. Sin
ella, un clúster nuevo no puede descifrar nada de lo que hay aquí. Ver
[RUNBOOK.md](RUNBOOK.md) para el respaldo, la restauración y cómo rotar una
contraseña.

## Convenciones

- **Un solo chart para todas las apps**, N ficheros de values. Nunca un chart
  por entorno ni por aplicación: divergen, y el fallo aparece solo en
  producción. Un arreglo en la forma de desplegar llega a todas a la vez.
- Los valores obligatorios de entorno usan `required` en las plantillas, así que
  olvidar uno rompe el `helm template` en el PR, no el despliegue en el clúster.
- Ningún fichero cambia sin pull request. Quién debe aprobarlo lo decide
  `CODEOWNERS` ruta por ruta: `dev.yaml` no tiene propietario y se mergea solo
  con el check en verde; todo lo demás exige al suyo. Si un pipeline pudiera
  escribir en `main`, la protección de rama no podría ser obligatoria.
- Cada aplicación corre en su propio `AppProject`: no puede desplegar en los
  namespaces de otra ni crear tipos de recurso que no usa.
