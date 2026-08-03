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
├── platform/                   # add-ons del clúster (ver su README)
└── apps/
    └── <app>/
        ├── chart/              # el chart de Helm: la FORMA de la aplicación
        ├── envs/               # dev.yaml, staging.yaml, prod.yaml
        └── applications/       # un Application de ArgoCD por entorno
```

Cada aplicación es una carpeta autocontenida. Añadir una app no obliga a tocar
las de nadie más.

## Cómo se despliega una versión

```
Release en el repo de código
   → CI construye y publica las imágenes
   → CI escribe el tag en apps/<app>/envs/dev.yaml          [automático]
   → ArgoCD sincroniza dev

Promoción a staging / prod
   → workflow promote.yml abre un Pull Request
   → un CODEOWNER lo revisa y lo mergea                     [puerta humana]
   → ArgoCD sincroniza ese entorno
```

La imagen **nunca se reconstruye** entre entornos: se promueve el mismo
artefacto que ya se validó en el entorno anterior.

## Cómo se hace rollback

`git revert` del commit de promoción. ArgoCD devuelve el clúster a la versión
anterior en el siguiente poll. No hay ningún comando de rollback que aprender:
el historial de git *es* el historial de despliegues.

## Cómo se añade una aplicación nueva

1. Crear `apps/<nombre>/` con `chart/`, `envs/` y `applications/`.
2. Commit y push.

El `root` detecta los Applications nuevos y los despliega. **No se ejecuta
`kubectl` en ningún momento.**

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

- Un chart por app, N ficheros de values. Nunca un chart por entorno: divergen,
  y el fallo aparece solo en producción.
- Los valores obligatorios de entorno usan `required` en las plantillas, así que
  olvidar uno rompe el `helm template` en el PR, no el despliegue en el clúster.
- `prod.yaml` solo cambia por pull request con revisor (ver `CODEOWNERS`).
