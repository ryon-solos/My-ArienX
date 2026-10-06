# GitHub source updates

The complete development project is <ArienX checkout>.
The installer-only copy Mark-LIV-main is retired.

ArienX checks the official ryon-solos/My-ArienX GitHub main branch at startup
and every six hours. A clean Git clone offers source updates in the UI.
After confirmation it applies only a fast-forward and asks for restart.
It never resets, stashes, deletes, or overwrites local source edits.
Ignored account settings, memory, and device identity stay in place.
Updates that touch private data paths are rejected. Dependency changes require
launcher/setup work rather than silently running pip or npm.

Your development checkout has local edits, so automatic updating is skipped.
Commit and push reviewed source changes to main to make them available to users.
Do not commit credentials or personal memory. Users need Git, repository access,
and an already configured Python/runtime environment. ZIP downloads without Git
history cannot use this updater. The future launcher must create a Git checkout
and handle dependency setup. No launcher is implemented in this phase.

A private GitHub repository requires each user's access. To serve public users,
publish the source in a public repository. Pushing to GitHub does not publish or
replace the existing Android APK; its release workflow is unchanged.
