ALTREX CODE V4 - GITHUB REPOSITORY PACK
=======================================

The folder ALTREX-CODE is a clean, ready-to-push repository:
  - complete source code (no node_modules, no build output, no keys, no personal files)
  - README.md for your subscribers
  - .github/workflows/ci.yml: GitHub automatically runs typecheck, tests and build
    on every push (Windows runner; no API keys needed)
  - .gitignore already excludes node_modules, build output, release files and .env files


Step 1 - Create the repository on GitHub
----------------------------------------
  On github.com: New repository -> name it (for example ALTREX-CODE)
  -> choose Private or Public -> do NOT add a README, .gitignore or license
  (the pack already has them) -> Create repository.


Step 2 - Push the code (PowerShell or Git Bash)
-----------------------------------------------
    cd ALTREX-CODE
    git init -b main
    git config user.name  "Your Name"
    git config user.email "you@example.com"
    git add .
    git commit -m "ALTREX CODE V4"
    git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
    git push -u origin main

  Replace YOUR-USERNAME / YOUR-REPO and your name/email with your own.
  After the push, open the "Actions" tab to see the CI checks run.


Step 3 - Publish the Windows app (optional)
-------------------------------------------
  Do not commit the .exe files into the repository (GitHub blocks files over 100 MB).
  Instead: on GitHub open Releases -> "Draft a new release" -> tag v4.0.0
  -> attach ALTREX-CODE-Setup-0.1.0-x64.exe, ALTREX-CODE-Portable-0.1.0-x64.exe and
  SHA256SUMS.txt (they are in the Full Pack, folder Windows-App) -> Publish release.


Before you publish
------------------
  - LICENSE: the pack has no license file. Without one, others may view the code but have
    no permission to reuse it. If you want subscribers to be allowed to use or modify
    it, add a LICENSE file of your choice (GitHub: Add file -> Create new file ->
    name it LICENSE -> "Choose a license template").
  - Never commit API keys or .env files. ALTREX stores keys encrypted on each user's
    computer, so no key is ever needed in the repository.
  - Delete this UPLOAD_TO_GITHUB.txt file after uploading (it is outside the repo folder).
