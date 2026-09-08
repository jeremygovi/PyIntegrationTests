# Tutoriel Kubernetes : ConfigMap

Ce scénario crée une ConfigMap dans un namespace existant, attend qu'elle soit lisible,
puis la supprime automatiquement, y compris si une assertion échoue. Le guide complet,
avec la préparation du kubeconfig et les commandes Docker, se trouve dans
[le tutoriel Kubernetes](../../docs/tutorial-kubernetes.md).

La protection `creates` vérifie avant la création que le nom est libre. Le framework
ajoute le label `pyintegrationtests.io/run`, vérifie que ce label appartient au run avant
toute suppression et confirme ensuite l'absence de la ressource.
