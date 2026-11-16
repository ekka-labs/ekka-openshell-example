# Sample plan

`explain-repo.json` asks the agent about the repositories on `code-desk`, using a platform prompt reference.
Run from this repository's folder on the server, as root (`sudo -i` first).

- Validate: `ekka plan validate plans/explain-repo.json`
- Create: `ekka plan create plans/explain-repo.json`
- Run: `ekka plan run ekka.example.openshell.explain.codeDesk.v1 --input 'question=What does the README say? Cite its file and line.'`
- Delete: `ekka plan delete ekka.example.openshell.explain.codeDesk.v1@1.0.0`
