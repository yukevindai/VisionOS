# Reflex — Cal Hacks 13

Native iPhone assistance app targeting iOS 17 and later. The repository name is `VisionOS`; the application runs on iPhone.

## Repository structure

```text
VisionOS/
├── README.md
├── docs/
│   ├── Cal_Hacks_13_Build_Spec_v3.md
│   └── R2_IOS_IMPLEMENTATION_SPEC.md
├── ios/
│   ├── Reflex.xcodeproj/         # R2 owns project wiring
│   ├── App/                     # R2
│   ├── Packages/
│   │   ├── Perception/          # R1
│   │   ├── Guidance/            # R1
│   │   ├── Tasks/               # R2
│   │   ├── HapticLink/          # R2
│   │   └── Targets/             # R3
│   ├── Tests/
│   └── UITests/
├── backend/                     # R3
├── firmware/                    # H1
├── hardware/                    # H1
├── contracts/                   # Agreed cross-team interfaces
├── fixtures/                    # Shared test inputs
└── .github/
    └── workflows/               # Automated checks
```

The [repository ownership guide](docs/REPOSITORY_STRUCTURE.md) records how these directories are used. Empty directories have `.gitkeep` placeholders so they survive a clone; their implementations and automated workflows await the responsible owners. The iOS project also includes `Config/`, `Scripts/`, and `Validation/` for build configuration, project generation, and validation records.

## Run and contribute

Open `ios/Reflex.xcodeproj` in Xcode and select the shared `Reflex` scheme. Live teammate integrations must be supplied; explicit simulation is available for local development.

- [iOS setup and test commands](ios/README.md)
- [Validation results](ios/Validation/2026-10-23.md)
- [R2 implementation specification](docs/R2_IOS_IMPLEMENTATION_SPEC.md)
- [Team build specification](docs/Cal_Hacks_13_Build_Spec_v3.md)

Local session relay and handoff files are ignored by Git and retained only on the developer's machine.
