# Premier exemple

Cet exemple fonctionne hors ligne. Il ne contacte aucun cloud et ne demande aucun
secret. Depuis la racine du dépôt :

```sh
make build
make example
```

`data.value` renvoie exactement la valeur placée dans `with.value`. Cette action sert à
apprendre la structure d'un scénario, à préparer une donnée ou à tester des assertions.
Le premier cas capture `replicas`, puis compare la capture à `inputs.expectedReplicas`.
Le second montre `name.generate`, qui produit un nom stable pendant un run et différent
d'un run à l'autre.

Modifiez par exemple `expectedReplicas: 2` en `expectedReplicas: 3`, relancez
`make example`, puis observez le diagnostic. Remettez ensuite la valeur à `2`.
