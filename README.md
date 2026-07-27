# platform-gitops

Repositorio GitOps de la plataforma. **Lo que está aquí es lo que corre en el
clúster.** ArgoCD reconcilia continuamente: cambiar algo en el clúster a mano
no cambia nada de forma duradera, se revierte solo.

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

## Qué NO va en este repositorio

**Secretos.** El repo es público y ArgoCD aplica todo lo que hay en él. Las
contraseñas viven en `Secret` de Kubernetes creados fuera de banda; el chart
solo guarda una referencia (`secretKeyRef`). En producción esto se resolvería
con Sealed Secrets o External Secrets Operator, que permiten versionar el
secreto cifrado.

## Convenciones

- Un chart por app, N ficheros de values. Nunca un chart por entorno: divergen,
  y el fallo aparece solo en producción.
- Los valores obligatorios de entorno usan `required` en las plantillas, así que
  olvidar uno rompe el `helm template` en el PR, no el despliegue en el clúster.
- `prod.yaml` solo cambia por pull request con revisor (ver `CODEOWNERS`).
