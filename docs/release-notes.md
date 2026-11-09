Both images passed cold boot, reboot, and settings persistence checks in this Actions run.

- `AvdLiteWithKsu-Android<version>-api<API>.zip`: includes the latest official stable KernelSU release resolved during the build, matched to the stock kernel KMI.
- `AvdLiteWithoutKsu-Android<version>-api<API>.zip`: the standard lightweight image.

Images and dependencies come directly from official remote sources. Each ZIP includes the system image, a 2-core / 4096 MB RAM / 32 GB / Resizable AVD template, an importer, file SHA-256 checksums, and build verification reports. Use the release asset digest to verify the ZIP.

Extract the archive and run with Python 3.11 or later:

```text
python import-avd.py --sdk <Android-SDK-directory>
```

The default AVD name is `AvdLiteWithKsu` or `AvdLiteWithoutKsu`. Use `--avd-home <directory>` to select the AVD data directory and `--name <name>` for an independent instance. The included JSON reports record versions, actual removals, and verification results.
