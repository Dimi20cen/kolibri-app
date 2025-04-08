from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("kolibri_app")

# Add any specific hidden imports related to the server process
hiddenimports += [
    "multiprocessing",
    "multiprocessing.spawn",
    "multiprocessing.reduction",
    "multiprocessing.context",
    "multiprocessing.pool",
]
