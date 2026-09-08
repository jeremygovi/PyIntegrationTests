# PyIntegrationTests

PyIntegrationTests permet d'écrire des tests d'intégration en YAML, puis de les exécuter avec pytest. Le moteur sait appeler Kubernetes, Helm, AWS, Cloudflare, HTTP et DNS, attendre un état asynchrone, vérifier des réponses et supprimer les ressources créées.

> **État du projet : alpha.** Utilisez d'abord un environnement de test isolé. Le framework refuse plusieurs opérations ambiguës ou dangereuses, mais il ne remplace pas les protections de votre infrastructure.

Tout le développement passe par Docker. Aucun Python, paquet, environnement virtuel ou `venv` ne doit être installé sur la machine hôte.

## Démarrage en cinq minutes

Prérequis : Docker avec Compose, et `make`.

```sh
make build       # construit l'environnement de développement
make example     # exécute un exemple local, sans compte ni infrastructure
make validate    # valide tous les exemples YAML
make test        # lance les tests unitaires et la couverture
make check       # lint, formatage, types et schémas JSON
```

L'exemple [getting-started](examples/getting-started/README.md) est le meilleur point de départ. Il fonctionne hors ligne :

```yaml
apiVersion: pyintegrationtests/v1alpha1
id: getting-started
name: Premiers pas
tests:
  - id: inspect-data
    name: Lire et vérifier une valeur
    steps:
      - kind: action
        id: sample
        action: data.value
        with:
          value:
            application: demo
            replicas: 2
        assertions:
          - path: $.replicas
            op: equal
            value: 2
```

La suite contient un test, lui-même composé d'une étape :

| Champ | Valeur | Rôle |
|---|---|---|
| `kind` | `action` | Exécute une action et vérifie éventuellement son résultat. L'autre valeur est `assert`, dédiée à l'observation. |
| `action` | `data.value` | Appelle l'action locale `value` du fournisseur `data`. Elle renvoie simplement la valeur reçue, ce qui est pratique pour apprendre et tester des références. |
| `with` | objet | Paramètres transmis à l'action. Ils dépendent de l'action choisie. |
| `assertions` | liste | Vérifications appliquées au résultat. Ici, `$.replicas` doit être égal à `2`. |

## Comprendre les mots-clés principaux

### `kind`

| Valeur | Effet |
|---|---|
| `action` | Lance une opération comme `http.request`, `kubernetes.resource` ou `data.value`. Peut ensuite vérifier et capturer le résultat. |
| `assert` | Lit une donnée déjà produite ou appelle une opération d'observation. Peut réessayer selon le `mode`. |

### `action`

Une action suit généralement la forme `fournisseur.opération`.

| Exemple | Effet |
|---|---|
| `data.value` | Renvoie la valeur fournie. Aucun accès externe. |
| `name.generate` | Génère un nom sûr pour une ressource. |
| `http.request` | Envoie une requête HTTP. |
| `kubernetes.resource` | Lit, crée, modifie ou supprime une ressource Kubernetes. |
| `helm.command` | Exécute une opération Helm. |
| `aws.call` | Appelle une opération AWS générique. |

La liste complète des actions, paramètres, valeurs autorisées et résultats se trouve dans la [référence complète](docs/reference.md).

### `mode`

Le champ `mode` s'utilise sur une étape `kind: assert` :

| Valeur | Comportement |
|---|---|
| `immediate` | Une seule vérification. Le test échoue immédiatement si l'assertion est fausse. |
| `eventually` | Réessaie jusqu'à ce que toutes les assertions soient vraies, ou jusqu'au `timeout`. Adapté aux systèmes asynchrones. |
| `consistently` | Vérifie pendant toute la durée du `timeout` que les assertions restent vraies. Adapté à une propriété qui ne doit jamais devenir fausse. |

`interval` fixe le délai en secondes entre deux observations. `timeout` fixe la durée maximale.

## Commandes Make

| Commande | Usage |
|---|---|
| `make help` | Affiche les commandes disponibles. |
| `make build` | Construit l'image Docker de développement. |
| `make example` | Exécute l'exemple local pour débutants. |
| `make validate SUITE=chemin` | Vérifie la syntaxe et la cohérence d'une suite. |
| `make list SUITE=chemin` | Liste les cas découverts. |
| `make collect SUITE=chemin` | Montre la collecte pytest sans exécuter les tests. |
| `make test` | Lance tous les tests avec couverture. |
| `make test-one TEST=tests/...` | Lance un fichier ou un test précis. `ARGS='-k expression'` est accepté. |
| `make check` | Exécute Ruff, mypy, Pyright et contrôle les schémas. |
| `make format` | Formate le code dans Docker. |
| `make schema` | Régénère les schémas JSON. |
| `make package` | Construit et vérifie le paquet Python. |
| `make image` | Construit l'image d'exécution locale. |
| `make run SUITE=... CONFIG=...` | Exécute une suite réelle avec `--live`. |
| `make cleanup JOURNAL=... CONFIG=...` | Rejoue le nettoyage d'une exécution interrompue. |

Les variables `SUITE`, `CONFIG`, `JOURNAL` et `ARGS` permettent d'adapter les commandes sans appeler Python localement.

## Exécuter un vrai test Kubernetes

Le tutoriel [Kubernetes pas à pas](docs/tutorial-kubernetes.md) crée une ConfigMap, attend qu'elle soit lisible, puis la supprime. Il requiert uniquement Docker, un fichier kubeconfig et un namespace de test existant.

```sh
cp examples/kubernetes-configmap/config.example.yaml \
  examples/kubernetes-configmap/config.local.yaml
# Renseigner context et namespace dans config.local.yaml

make validate \
  SUITE=examples/kubernetes-configmap/configmap.integ.yaml \
  ARGS='--config examples/kubernetes-configmap/config.local.yaml'

make run \
  SUITE=examples/kubernetes-configmap/configmap.integ.yaml \
  CONFIG=examples/kubernetes-configmap/config.local.yaml
```

Le kubeconfig doit être monté dans `/credentials` par `compose.live.yaml`. Le tutoriel explique le chemin attendu et chaque étape avant toute exécution réelle.

## Organisation de la documentation

- [Premiers pas](docs/getting-started.md) : lire, valider et exécuter une première suite.
- [Référence YAML](docs/reference.md) : tous les champs, actions, opérateurs et valeurs autorisées.
- [DSL et références](docs/dsl.md) : composition des suites, `$ref`, captures et assertions.
- [Configuration](docs/configuration.md) : fournisseurs, identifiants et variables d'environnement.
- [Nettoyage](docs/cleanup.md) : cycle de vie, propriété et journal de reprise.
- [Tutoriel Kubernetes](docs/tutorial-kubernetes.md) : exemple réel guidé.
- [Extensions](docs/plugins.md) : ajouter un fournisseur ou une action.
- [Contribution](CONTRIBUTING.md) : workflow de développement entièrement Docker.

Des exemples complets sont fournis pour [les premiers pas](examples/getting-started/README.md), [Kubernetes](examples/kubernetes-configmap/README.md), [Helm](examples/helm-workload/README.md) et [Cloudflare](examples/cloudflare-record/README.md).

## Sécurité et nettoyage

Une ressource déclarée dans `creates` est enregistrée dans un journal avant la suite du test. Son bloc `owned` doit prouver qu'elle appartient à l'exécution courante avant suppression. Le nettoyage est tenté en fin de test et peut être relancé avec `make cleanup` après une interruption. Consultez [Nettoyage](docs/cleanup.md) avant d'écrire un test qui crée des ressources.

Les valeurs sensibles doivent venir de variables d'environnement référencées par la configuration. Les rapports masquent les champs déclarés sensibles. Ne placez jamais de secret directement dans une suite YAML ou dans Git.

## Compatibilité

Python 3.12 à 3.14 dans les images Docker du projet. Les fichiers collectés portent l'extension `*.integ.yaml` ou `*.integ.yml`. L'interface en cours de stabilisation est `pyintegrationtests/v1alpha1`.

Aucune licence de redistribution n'est accordée pour ce dépôt.
