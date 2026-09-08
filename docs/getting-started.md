# Premiers pas

Ce guide ne demande que Docker, Docker Compose et `make`. Toutes les commandes Python s'exécutent dans le conteneur du projet.

## Construire et exécuter le premier exemple

```sh
make build
make example
```

L'exemple `examples/getting-started/basic.integ.yaml` est entièrement local. Il ne contacte aucun cluster ni service cloud.

Une **suite** est un fichier `*.integ.yaml`. Elle contient des **tests**, eux-mêmes composés de **steps** exécutés dans l'ordre.

```yaml
apiVersion: pyintegrationtests/v1alpha1
id: demo
name: Ma première suite
tests:
  - id: value-is-correct
    name: Vérifier une donnée
    steps:
      - kind: action
        id: load
        action: data.value
        with:
          value: {status: ready, replicas: 2}
        assertions:
          - path: $.status
            op: equal
            value: ready
```

`data.value` renvoie exactement `with.value`. L'assertion lit `status` avec le chemin JSONPath `$.status`, puis le compare à `ready`.

## Valider et inspecter

```sh
make validate SUITE=examples/getting-started/basic.integ.yaml
make list SUITE=examples/getting-started/basic.integ.yaml
make collect SUITE=examples/getting-started/basic.integ.yaml
```

- `validate` détecte les champs inconnus, valeurs interdites et références invalides ;
- `list` affiche les cas compris par le moteur ;
- `collect` montre les tests tels que pytest les collectera.

## Capturer et réutiliser une valeur

```yaml
captures:
  - name: replica_count
    path: $.replicas
```

Une étape suivante peut la relire :

```yaml
with:
  value:
    $ref: captures.replica_count
```

Les références peuvent viser `inputs`, `config`, `run`, `captures` et les résultats des étapes. La [référence YAML](reference.md) donne toutes les formes acceptées.

## Passer à un fournisseur réel

Une action externe indique une instance configurée avec `provider`. Les exécutions externes exigent volontairement `make run ... CONFIG=...`, qui active le mode live. Commencez avec le [tutoriel Kubernetes](tutorial-kubernetes.md) dans un environnement isolé.

En cas d'erreur, lisez le chemin indiqué, par exemple `tests[0].steps[1].with.name`. Les causes fréquentes sont une indentation incorrecte, un paramètre obligatoire absent ou un `$ref` vers un identifiant inexistant. `make validate` les détecte sans modifier l'infrastructure.
