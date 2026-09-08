# Référence YAML

Cette page décrit l'API `pyintegrationtests/v1alpha1`. Les champs inconnus sont refusés afin de détecter les fautes de frappe.

## Suite et test

| Niveau | Champ | Requis | Valeur | Rôle |
|---|---|---:|---|---|
| suite | `apiVersion` | oui | `pyintegrationtests/v1alpha1` | Version du format. |
| suite | `id`, `name` | oui | texte | Identité stable et nom lisible. |
| suite | `config` | non | chemin | Configuration par défaut. |
| suite | `labels`, `inputs` | non | objet | Métadonnées et données accessibles par `$ref`. |
| suite | `sensitiveInputs` | non | liste | Inputs à masquer dans les rapports. |
| suite | `defaults.timeout` | non | secondes | Timeout par défaut. |
| suite | `tests` | oui | liste non vide | Cas pytest. |
| test | `id`, `name` | oui | texte | Identité du cas. |
| test | `labels`, `timeout` | non | objet, secondes | Métadonnées et limite du cas. |
| test | `steps` | oui | liste non vide | Étapes exécutées dans l'ordre. |

## Étapes et modes

Chaque étape possède `id` et `kind`.

| `kind` | Champs | Effet |
|---|---|---|
| `action` | `action`, `provider`, `with` | Exécute une opération. Accepte aussi `assertions`, `captures`, `creates`, `tracks`, `uses`, `expectedError`, `sensitive`. |
| `assert` | exactement un de `source` ou `observe`, puis `assertions` | Vérifie une donnée. Accepte `mode`, `timeout`, `interval`, `failWhen`, `captures`, `sensitive`. |

| `mode` | Effet |
|---|---|
| `immediate` | Une seule observation. |
| `eventually` | Réessaie jusqu'au succès ou au `timeout`. |
| `consistently` | Les assertions doivent rester vraies pendant tout le `timeout`. |

`observe` appelle une action. Son option `notFound: error|null` décide si une absence lève une erreur ou produit `null`. `timeout` est limité à 86 400 secondes et `interval` à 300 secondes.

## Assertions

Une assertion contient `path` (défaut `$`) et `op`.

| Famille | Opérateurs | Donnée complémentaire |
|---|---|---|
| Comparaison | `equal`, `notEqual`, `greater`, `greaterOrEqual`, `less`, `lessOrEqual` | `value` |
| Présence | `exists`, `notExists`, `isNull`, `isNotNull` | aucune |
| Contenu | `contains`, `notContains`, `isSubset`, `isNotSubset` | `value` |
| Forme | `lengthEqual`, `isType`, `isEmpty`, `isNotEmpty`, `matchRegex` | selon l'opérateur |
| Composition | `allOf`, `anyOf`, `not` | `checks` |
| Extension | `custom` | `assertion` |

Options : `quantifier: scalar|any|all`, `allowEmpty`, `order: ordered|set|multiset`, `transforms` et `default`. Transformations : `decodeBase64`, `decodeJson`, `sort`, `lower`, `stripTrailingDot`, `decimal`.

## Références et captures

```yaml
value: {$ref: inputs.expected}
value: {$ref: steps.create.data.metadata.name}
value: {$ref: captures.generated_name}
```

Sources : `inputs`, `run`, `config`, `captures`, `steps.<id>.data` et `steps.<id>.meta`. `$format` assemble du texte. `$literal` protège une valeur littérale. Une capture accepte `name`, `path`, `transforms` et `sensitive`.

## Actions disponibles

| Action | Paramètres `with` | Effet |
|---|---|---|
| `data.value` | `value` | Renvoie la valeur telle quelle. |
| `name.generate` | `prefix`, `maxLength` (16–253, défaut 63) | Génère `{name}`. |
| `artifact.zip` | `files`, `name` | Crée une archive, renvoie chemin, empreintes et taille. |
| `artifact.read` | `path`, `encoding: text|base64|json` | Lit un artefact. |
| `artifact.hash` | `value`, `algorithm: sha256|sha512` | Calcule une empreinte. |
| `jwt.sign` | `keyEnv`, `algorithm`, `claims`, `headers` | Signe un JWT. Algorithmes RS256/384/512, ES256, HS256. |
| `http.request` | `method`, `url`, `headers`, `query`, `body` ou `jsonBody`, options de redirection/statut | Requête HTTP. Méthodes GET, HEAD, OPTIONS, POST, PUT, PATCH, DELETE. |
| `dns.query` | `name`, `recordType` (défaut A) | Requête DNS. |
| `kubernetes.resource` | `operation`, `apiVersion`, `kind`, `namespace`, `name`, sélecteurs, `body`, patch/readiness | get, list, create, apply, patch ou delete. Patch merge/json/strategic. |
| `kubernetes.logs` | `namespace`, `name`, `container`, `tailLines`, `sinceSeconds` | Lit les logs. |
| `kubernetes.exec` | `namespace`, `name`, `container`, `argv` | Exécute une commande. |
| `kubernetes.job` | `namespace`, `source`, `name`, `fromCronJob`, `labels` | Crée un Job depuis une source. |
| `helm.command` | `operation`, `release`, `chart`, `version`, valeurs, `wait`, `workspaceRoot` | install, upgrade, uninstall, status, get-values, get-manifest, dependency-build. |
| `aws.call` | `service`, `operation`, `parameters`, `pagination: page|all`, `maxPages`, `readStreams` | Appel boto3 générique. |
| `aws.s3.empty` | `bucket`, `deleteBucket`, `maxPages` | Vide un bucket. |
| `aws.iam.delete-role` | `roleName`, `maxPages` | Supprime politiques attachées puis rôle. |
| `aws.kms.find` | `tags`, `maxPages` | Recherche des clés. |
| `aws.kms.schedule-deletion` | `keyId`, `pendingWindowInDays` (7–30), `maxPages` | Programme la suppression. |
| `cloudflare.request` | `method`, `path`, `query`, `body`, `pagination`, `maxPages` | Requête Cloudflare GET/POST/PUT/PATCH/DELETE. |

Les contraintes machine lisibles sont dans `schemas/` et contrôlées par `make check`.

## Création, nettoyage et erreurs attendues

Une ressource dans `creates` ou `tracks` accepte `id`, `probe`, `absent`, `owned`, `cleanup`, `verify`, `cleaned` et `after`. `probe` retrouve la ressource, `owned` prouve son appartenance au test, `cleanup` la supprime, `verify` peut revérifier sa propriété et `cleaned` confirme son absence. `after` impose l'ordre. Voir [Nettoyage](cleanup.md).

`expectedError` accepte `provider`, `code` et `status`. Au moins `code` ou `status` est obligatoire et tous les critères fournis doivent correspondre.
