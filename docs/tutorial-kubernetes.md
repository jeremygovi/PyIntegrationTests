# Tutoriel Kubernetes : créer et nettoyer une ConfigMap

Ce tutoriel utilise Docker et un kubeconfig existant. Choisissez un cluster de développement et un namespace dédié déjà créé. Le framework ne crée pas de cluster.

## Préparer les fichiers locaux

```sh
mkdir -p .pyintegrationtests/credentials
cp /chemin/vers/votre/kubeconfig .pyintegrationtests/credentials/kubeconfig
cp examples/kubernetes-configmap/config.example.yaml \
  examples/kubernetes-configmap/config.local.yaml
```

Ces chemins sont ignorés par Git. Dans `config.local.yaml`, remplacez les valeurs d'exemple :

```yaml
providers:
  cluster:
    kind: kubernetes
    context: mon-contexte-de-test
    namespace: integration-tests
    kubeconfig: /credentials/kubeconfig
```

`/credentials/kubeconfig` est le chemin dans le conteneur. `compose.live.yaml` monte `.pyintegrationtests/credentials` à cet emplacement en lecture seule.

## Valider sans toucher au cluster

```sh
make validate \
  SUITE=examples/kubernetes-configmap/configmap.integ.yaml \
  ARGS='--config examples/kubernetes-configmap/config.local.yaml'
```

La validation charge le YAML et la configuration, mais n'exécute aucune action Kubernetes.

## Comprendre puis lancer le scénario

Le scénario génère un nom, crée une ConfigMap, l'inscrit dans le journal de nettoyage, l'observe avec `mode: eventually`, puis vérifie sa propriété avant de la supprimer.

```sh
make run \
  SUITE=examples/kubernetes-configmap/configmap.integ.yaml \
  CONFIG=examples/kubernetes-configmap/config.local.yaml
```

`make run` ajoute explicitement `--live`. La preuve de propriété repose sur le label `pyintegrationtests.io/run`, ajouté par le moteur, afin de ne pas supprimer une ressource étrangère portant le même nom.

## Reprendre un nettoyage interrompu

Le chemin du journal apparaît dans les sorties. S'il reste une ressource :

```sh
make cleanup \
  JOURNAL=.pyintegrationtests/journals/chemin-du-journal.jsonl \
  CONFIG=examples/kubernetes-configmap/config.local.yaml
```

| Symptôme | Vérification |
|---|---|
| kubeconfig introuvable | Le fichier hôte est `.pyintegrationtests/credentials/kubeconfig`; la configuration garde `/credentials/kubeconfig`. |
| contexte inconnu | `context` doit correspondre exactement à une entrée du kubeconfig. |
| accès refusé | Le compte doit pouvoir lire, créer et supprimer des ConfigMaps dans le namespace. |
| authentification externe absente | Certains kubeconfigs appellent un binaire absent de l'image. Utilisez un kubeconfig autonome ou adaptez une image privée. |
| nettoyage refusé | La preuve `owned` ne correspond pas. Inspectez la ressource avant toute intervention manuelle. |

Ne ciblez jamais un cluster de production pour découvrir le framework.
