# platform/

Todo lo que define **cómo** se despliega, frente a `apps/`, que define **qué**
se despliega. Es lo único que `bootstrap/root.yaml` gestiona directamente.

- `projects/` — un `AppProject` por aplicación. Limita, para cada una, de qué
  repositorio puede leer, en qué namespaces puede desplegar y qué tipos de
  recurso puede crear. Es el aislamiento entre aplicaciones, y por tanto lo que
  impide que un error en una toque los namespaces de otra.
- `applicationsets/` — un único `ApplicationSet` que genera todas las
  `Application` a partir de los ficheros de `apps/*/envs/*.yaml`. Añadir un
  entorno es añadir un fichero de valores; no hay una `Application` que
  mantener en paralelo.

Aquí irían también los add-ons del clúster: ingress controller, metrics-server,
cert-manager, stack de observabilidad. Todo lo que las aplicaciones **usan**
pero que no pertenece a ninguna de ellas. En este clúster de Minikube el
Ingress y el metrics-server vienen de addons (`minikube addons enable ...`) y
ArgoCD se instala a mano — decisiones razonables en un portátil. En un clúster
real se gestionarían igual que las apps: como `Application` apuntando a los
charts oficiales, con sus values versionados aquí.

La separación **plataforma / aplicaciones** es intencional:

- `apps/` cambia cuando el equipo de desarrollo entrega una versión.
- `platform/` cambia cuando el equipo de infraestructura cambia las reglas.

Son dos ciclos de vida distintos, con revisores distintos y ritmos distintos.
Mezclarlos hace que un cambio en las reglas de aislamiento y un despliegue de
una app compitan por la misma revisión.
