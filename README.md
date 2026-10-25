# VisionOS — Cal Hacks 13

Native iPhone assistance app targeting iOS 17 and later. The repository name is `VisionOS`; the application runs on iPhone.

## Repository structure

```text
VisionOS/
├── README.md
├── docs/
│   ├── Cal_Hacks_13_Build_Spec_v3.md
│   ├── R2_IOS_IMPLEMENTATION_SPEC.md
│   └── REPOSITORY_STRUCTURE.md
├── ios/
│   ├── VisionOS.xcodeproj/         # R2 owns project wiring
│   ├── App/                     # R2
│   ├── Packages/
│   │   ├── Perception/          # R1
│   │   │   └── Prototypes/
│   │   │       └── DeskGuide-Laptop/ # Python research harness
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

The [repository ownership guide](docs/REPOSITORY_STRUCTURE.md) records how these directories are used. Empty owner directories have `.gitkeep` placeholders so they survive a clone; their implementations await the responsible owners. The iOS project also includes `Config/`, `Scripts/`, and `Validation/` for build configuration, project generation, and validation records. The [iOS CI workflow](.github/workflows/ios-ci.yml) contains the automated checks; hosted execution remains unverified.

The [DeskGuide laptop prototype](ios/Packages/Perception/Prototypes/DeskGuide-Laptop/README.md) lives with R1's perception work. Its Python source, evaluation tools, fixtures, licenses, and reports stay together. It is a research reference for perception and guidance; native Perception and Guidance packages still need to be supplied and integrated.

## Family Notes and Family Voice

Trusted family members leave short notes that the VisionOS user hears at the right moment (between
tasks, or when they find the object a note is about), in the family member's own consented voice
(Cartesia). Vision, Cartesia features, responsible-use rules and limitations:
[docs/FAMILY_VOICE.md](docs/FAMILY_VOICE.md). Implementation: [docs/FAMILY_NOTES.md](docs/FAMILY_NOTES.md).

## Run and contribute

Open `ios/VisionOS.xcodeproj` in Xcode and select the shared `VisionOS` scheme. Live teammate integrations must be supplied; explicit simulation is available for local development.

- [iOS setup and test commands](ios/README.md)
- [Validation results](ios/Validation/2026-10-23.md)
- [R2 implementation specification](docs/R2_IOS_IMPLEMENTATION_SPEC.md)
- [Team build specification](docs/Cal_Hacks_13_Build_Spec_v3.md)

Local session relay and handoff files are ignored by Git and retained only on the developer's machine.
