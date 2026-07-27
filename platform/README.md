# platform/

Add-ons del clúster: ingress controller, metrics-server, cert-manager, stack de
observabilidad. Todo lo que las aplicaciones **usan** pero que no pertenece a
ninguna de ellas.

Está vacío a propósito. En este clúster de Minikube, el Ingress y el
metrics-server vienen de addons (`minikube addons enable ...`), y ArgoCD se
instala a mano — decisiones razonables en un portátil.

En un clúster real, estos componentes se gestionarían igual que las apps: como
`Application` de ArgoCD apuntando a los charts oficiales, con sus values
versionados aquí. La carpeta existe para marcar ese sitio y para dejar claro
que la separación **plataforma / aplicaciones** es intencional:

- `apps/` cambia cuando el equipo de desarrollo entrega una versión.
- `platform/` cambia cuando el equipo de infraestructura actualiza el clúster.

Son dos ciclos de vida distintos, con revisores distintos y ritmos distintos.
Mezclarlos hace que una actualización del ingress controller y un despliegue de
una app compitan por la misma revisión.
