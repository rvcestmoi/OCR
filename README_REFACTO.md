# Refacto réalisée

## Objectif
Alléger `ui/main_window.py` et rendre les chemins configurables plus simplement.

## Ce qui a été fait
- `ui/main_window.py` ne contient plus que la classe principale et son `__init__`.
- Les méthodes ont été réparties dans `ui/mainwindow/` par responsabilité :
  - `core_mixin.py` : OCR, champs, sauvegarde JSON, modèle fournisseur
  - `transport_tables_mixin.py` : tables dossiers / TVA / transporteur / tournée
  - `documents_mixin.py` : navigation documents, chargement dossier, statuts visuels
  - `validation_mixin.py` : validation, frais, suppression, états de traitement
  - `cmr_mixin.py` : rattachement CMR / relink documents
  - `links_mixin.py` : récupération des liens et post-traitement
  - `workers.py` : workers Qt pour téléchargement / post-process
  - `common.py` : imports partagés et constantes communes

## Configuration des chemins
Deux nouveaux fichiers source ont été ajoutés :
- `app/paths.py`
- `app/settings.py`

Le plus simple pour changer les chemins globaux est `app/paths.py`.

Variables disponibles dans `app/paths.py` :
- `DEFAULT_PDF_FOLDER`
- `MODELS_DIR`
- `SUPPLIERS_DIR`

Ces chemins peuvent aussi être surchargés par variables d'environnement :
- `OCR_PDF_FOLDER`
- `OCR_MODELS_DIR`

## Remarque
La refacto a été vérifiée côté syntaxe Python (`py_compile`).
Le lancement complet de l'interface n'a pas pu être rejoué ici car l'environnement de travail ne contient pas PySide6.
