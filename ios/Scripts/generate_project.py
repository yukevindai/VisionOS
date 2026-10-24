#!/usr/bin/env python3
"""Regenerate Reflex.xcodeproj after adding or removing Swift source files.

The generator uses only the Python standard library. It scans App, Tests, and
UITests so the committed project and target membership stay in sync.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


IOS = Path(__file__).resolve().parents[1]
PROJECT = IOS / "Reflex.xcodeproj" / "project.pbxproj"


def uid(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:24].upper()


def q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def list_block(values: list[str], indent: str = "\t\t\t") -> str:
    return "(\n" + "".join(f"{indent}{value},\n" for value in values) + "\t\t)"


def source_group(name: str, path: str, files: list[Path]) -> tuple[str, list[str], list[str]]:
    group_id = uid(f"group:{name}")
    base_path = IOS / path
    children: list[str] = []
    refs: list[str] = []
    for file in files:
        rel = file.relative_to(IOS).as_posix()
        ref_id = uid(f"file:{rel}")
        children.append(ref_id)
        refs.append(
            f"\t\t{ref_id} /* {file.name} */ = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = {q(file.relative_to(base_path).as_posix())}; sourceTree = \"<group>\"; }};"
        )
    group = (
        f"\t\t{group_id} /* {name} */ = {{isa = PBXGroup; children = {list_block(children)}; "
        f"path = {q(path)}; sourceTree = \"<group>\"; }};"
    )
    return group, refs, [uid(f"file:{p.relative_to(IOS).as_posix()}") for p in files]


def build_files(files: list[Path], phase: str) -> tuple[list[str], list[str]]:
    builds: list[str] = []
    ids: list[str] = []
    for file in files:
        rel = file.relative_to(IOS).as_posix()
        file_id = uid(f"file:{rel}")
        build_id = uid(f"build:{phase}:{rel}")
        ids.append(build_id)
        builds.append(f"\t\t{build_id} /* {file.name} in {phase} */ = {{isa = PBXBuildFile; fileRef = {file_id} /* {file.name} */; }};")
    return builds, ids


def main() -> None:
    app_files = sorted((IOS / "App").rglob("*.swift"))
    unit_files = sorted((IOS / "Tests" / "ReflexAppTests").rglob("*.swift"))
    ui_files = sorted((IOS / "UITests").rglob("*.swift"))

    app_group, app_refs, app_file_ids = source_group("App", "App", app_files)
    unit_group, unit_refs, unit_file_ids = source_group("ReflexAppTests", "Tests/ReflexAppTests", unit_files)
    ui_group, ui_refs, ui_file_ids = source_group("ReflexUITests", "UITests", ui_files)

    app_builds, app_build_ids = build_files(app_files, "Sources")
    unit_builds, unit_build_ids = build_files(unit_files, "Sources")
    ui_builds, ui_build_ids = build_files(ui_files, "Sources")

    refs = "\n".join(app_refs + unit_refs + ui_refs)
    builds = "\n".join(app_builds + unit_builds + ui_builds)
    config_refs = f"""\
\t\t{uid('resource:Models')} /* Models */ = {{isa = PBXFileReference; lastKnownFileType = folder; path = Resources/Models; sourceTree = SOURCE_ROOT; }};
\t\t{uid('resource:Assets')} /* Assets.xcassets */ = {{isa = PBXFileReference; lastKnownFileType = folder.assetcatalog; path = Resources/Assets.xcassets; sourceTree = SOURCE_ROOT; }};
\t\t{uid('config:Debug.xcconfig')} /* Debug.xcconfig */ = {{isa = PBXFileReference; lastKnownFileType = text.xcconfig; path = Config/Debug.xcconfig; sourceTree = SOURCE_ROOT; }};
\t\t{uid('config:Release.xcconfig')} /* Release.xcconfig */ = {{isa = PBXFileReference; lastKnownFileType = text.xcconfig; path = Config/Release.xcconfig; sourceTree = SOURCE_ROOT; }};
\t\t{uid('config:Local.example.xcconfig')} /* Local.example.xcconfig */ = {{isa = PBXFileReference; lastKnownFileType = text.xcconfig; path = Config/Local.example.xcconfig; sourceTree = SOURCE_ROOT; }};
\t\t{uid('app:Info.plist')} /* Info.plist */ = {{isa = PBXFileReference; lastKnownFileType = text.plist.xml; path = App/Info.plist; sourceTree = SOURCE_ROOT; }};
"""
    product_refs = f"""\
\t\t{uid('product:Reflex.app')} /* Reflex.app */ = {{isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = Reflex.app; sourceTree = BUILT_PRODUCTS_DIR; }};
\t\t{uid('product:ReflexAppTests.xctest')} /* ReflexAppTests.xctest */ = {{isa = PBXFileReference; explicitFileType = wrapper.cfbundle; includeInIndex = 0; path = ReflexAppTests.xctest; sourceTree = BUILT_PRODUCTS_DIR; }};
\t\t{uid('product:ReflexUITests.xctest')} /* ReflexUITests.xctest */ = {{isa = PBXFileReference; explicitFileType = wrapper.cfbundle; includeInIndex = 0; path = ReflexUITests.xctest; sourceTree = BUILT_PRODUCTS_DIR; }};
"""

    project_id = uid("project:Reflex")
    app_target = uid("target:Reflex")
    unit_target = uid("target:ReflexAppTests")
    ui_target = uid("target:ReflexUITests")
    app_sources = uid("phase:app-sources")
    unit_sources = uid("phase:unit-sources")
    ui_sources = uid("phase:ui-sources")
    app_frameworks = uid("phase:app-frameworks")
    unit_frameworks = uid("phase:unit-frameworks")
    ui_frameworks = uid("phase:ui-frameworks")
    app_resources = uid("phase:app-resources")
    app_config_list = uid("configlist:app")
    unit_config_list = uid("configlist:unit")
    ui_config_list = uid("configlist:ui")
    project_config_list = uid("configlist:project")
    # CredentialsManager defaults reference this transitive product from app code.
    # Link it explicitly when Xcode emits dynamic package products.
    package_keychain = uid("package:SimpleKeychain")
    dep_keychain = uid("package-product:SimpleKeychain")
    package_auth0 = uid("package:Auth0")
    dep_auth0 = uid("package-product:Auth0")
    package_livekit = uid("package:LiveKit")
    dep_livekit = uid("package-product:LiveKit")
    package_guidance = uid("package:Guidance")
    dep_guidance = uid("package-product:Guidance")
    package_interpretation = uid("package:Targets")
    dep_interpretation = uid("package-product:Targets")
    package_tasks = uid("package:Tasks")
    package_haptics = uid("package:HapticLink")
    dep_tasks = uid("package-product:Tasks")
    dep_haptics = uid("package-product:HapticLink")
    app_product_dep = uid("dependency:unit-test-host")
    target_proxy = uid("proxy:ui-test-host")
    target_dependency = uid("dependency:ui-test-host")

    package_targets = f"""\
\t\t{dep_guidance} /* Guidance */ = {{isa = XCSwiftPackageProductDependency; package = {package_guidance}; productName = Guidance; }};
\t\t{dep_interpretation} /* Targets */ = {{isa = XCSwiftPackageProductDependency; package = {package_interpretation}; productName = Targets; }};
\t\t{dep_keychain} /* SimpleKeychain */ = {{isa = XCSwiftPackageProductDependency; package = {package_keychain}; productName = SimpleKeychain; }};
\t\t{dep_auth0} /* Auth0 */ = {{isa = XCSwiftPackageProductDependency; package = {package_auth0}; productName = Auth0; }};
\t\t{dep_livekit} /* LiveKit */ = {{isa = XCSwiftPackageProductDependency; package = {package_livekit}; productName = LiveKit; }};
\t\t{dep_tasks} /* Tasks */ = {{isa = XCSwiftPackageProductDependency; package = {package_tasks}; productName = Tasks; }};
\t\t{dep_haptics} /* HapticLink */ = {{isa = XCSwiftPackageProductDependency; package = {package_haptics}; productName = HapticLink; }};
"""
    package_builds = f"""\
\t\t{uid('package-build:Guidance')} /* Guidance in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_guidance}; }};
\t\t{uid('package-build:Guidance-unit')} /* Guidance in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_guidance}; }};
\t\t{uid('resource-build:Models')} /* Models in Resources */ = {{isa = PBXBuildFile; fileRef = {uid('resource:Models')}; }};
\t\t{uid('resource-build:Assets')} /* Assets in Resources */ = {{isa = PBXBuildFile; fileRef = {uid('resource:Assets')}; }};
\t\t{uid('package-build:Targets')} /* Targets in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_interpretation}; }};
\t\t{uid('package-build:Targets-unit')} /* Targets in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_interpretation}; }};
\t\t{uid('package-build:SimpleKeychain')} /* SimpleKeychain in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_keychain}; }};
\t\t{uid('package-build:SimpleKeychain-unit')} /* SimpleKeychain in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_keychain}; }};
\t\t{uid('package-build:Auth0')} /* Auth0 in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_auth0}; }};
\t\t{uid('package-build:Auth0-unit')} /* Auth0 in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_auth0}; }};
\t\t{uid('package-build:LiveKit')} /* LiveKit in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_livekit}; }};
\t\t{uid('package-build:LiveKit-unit')} /* LiveKit in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_livekit}; }};
\t\t{uid('package-build:Tasks')} /* Tasks in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_tasks}; }};
\t\t{uid('package-build:HapticLink')} /* HapticLink in Frameworks */ = {{isa = PBXBuildFile; productRef = {dep_haptics}; }};
"""
    source_tree_children = [uid("resource:Assets"), uid("resource:Models"), uid("group:App"), uid("group:ReflexAppTests"), uid("group:ReflexUITests"), uid("config:Debug.xcconfig"), uid("config:Release.xcconfig"), uid("config:Local.example.xcconfig"), uid("app:Info.plist"), uid("group:Products")]
    product_children = [uid("product:Reflex.app"), uid("product:ReflexAppTests.xctest"), uid("product:ReflexUITests.xctest")]

    text = f'''// !$*UTF8*$!
{{
\tarchiveVersion = 1;
\tclasses = {{}};
\tobjectVersion = 56;
\tobjects = {{

/* Begin PBXBuildFile section */
{builds}
{package_builds}/* End PBXBuildFile section */

/* Begin PBXContainerItemProxy section */
\t\t{target_proxy} /* PBXContainerItemProxy */ = {{isa = PBXContainerItemProxy; containerPortal = {project_id}; proxyType = 1; remoteGlobalIDString = {app_target}; remoteInfo = Reflex; }};
/* End PBXContainerItemProxy section */

/* Begin PBXFileReference section */
{refs}
{config_refs}{product_refs}/* End PBXFileReference section */

/* Begin PBXFrameworksBuildPhase section */
\t\t{app_frameworks} /* Frameworks */ = {{isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = {list_block([uid('package-build:Guidance'), uid('package-build:Targets'), uid('package-build:Tasks'), uid('package-build:HapticLink'), uid('package-build:LiveKit'), uid('package-build:Auth0'), uid('package-build:SimpleKeychain')])}; runOnlyForDeploymentPostprocessing = 0; }};
\t\t{unit_frameworks} /* Frameworks */ = {{isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = {list_block([uid('package-build:Guidance-unit'), uid('package-build:Targets-unit'), uid('package-build:LiveKit-unit'), uid('package-build:Auth0-unit'), uid('package-build:SimpleKeychain-unit')])}; runOnlyForDeploymentPostprocessing = 0; }};
\t\t{ui_frameworks} /* Frameworks */ = {{isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; }};
/* End PBXFrameworksBuildPhase section */

/* Begin PBXGroup section */
{app_group}
{unit_group}
{ui_group}
\t\t{uid('group:Products')} /* Products */ = {{isa = PBXGroup; children = {list_block(product_children)}; name = Products; sourceTree = "<group>"; }};
\t\t{uid('group:Main')} /* Main Group */ = {{isa = PBXGroup; children = {list_block(source_tree_children)}; sourceTree = "<group>"; }};
/* End PBXGroup section */

/* Begin PBXNativeTarget section */
\t\t{app_target} /* Reflex */ = {{isa = PBXNativeTarget; buildConfigurationList = {app_config_list}; buildPhases = ({app_sources}, {app_frameworks}, {app_resources}, ); buildRules = (); dependencies = (); name = Reflex; packageProductDependencies = ({dep_guidance}, {dep_interpretation}, {dep_tasks}, {dep_haptics}, {dep_livekit}, {dep_auth0}, {dep_keychain}, ); productName = Reflex; productReference = {uid('product:Reflex.app')}; productType = "com.apple.product-type.application"; }};
\t\t{unit_target} /* ReflexAppTests */ = {{isa = PBXNativeTarget; buildConfigurationList = {unit_config_list}; buildPhases = ({unit_sources}, {unit_frameworks}, ); buildRules = (); dependencies = ({app_product_dep}, ); name = ReflexAppTests; packageProductDependencies = ({dep_guidance}, {dep_interpretation}, {dep_livekit}, {dep_auth0}, {dep_keychain}, ); productName = ReflexAppTests; productReference = {uid('product:ReflexAppTests.xctest')}; productType = "com.apple.product-type.bundle.unit-test"; }};
\t\t{ui_target} /* ReflexUITests */ = {{isa = PBXNativeTarget; buildConfigurationList = {ui_config_list}; buildPhases = ({ui_sources}, {ui_frameworks}, ); buildRules = (); dependencies = ({target_dependency}, ); name = ReflexUITests; packageProductDependencies = (); productName = ReflexUITests; productReference = {uid('product:ReflexUITests.xctest')}; productType = "com.apple.product-type.bundle.ui-testing"; }};
/* End PBXNativeTarget section */

/* Begin PBXProject section */
\t\t{project_id} /* Project object */ = {{isa = PBXProject; attributes = {{BuildIndependentTargetsInParallel = 1; LastUpgradeCheck = 2660; TargetAttributes = {{{app_target} = {{CreatedOnToolsVersion = 26.6; }}; {unit_target} = {{CreatedOnToolsVersion = 26.6; TestTargetID = {app_target}; }}; {ui_target} = {{CreatedOnToolsVersion = 26.6; TestTargetID = {app_target}; }}; }}; }}; buildConfigurationList = {project_config_list}; compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en, Base, ); mainGroup = {uid('group:Main')}; packageReferences = ({package_guidance}, {package_interpretation}, {package_tasks}, {package_haptics}, {package_livekit}, {package_auth0}, {package_keychain}, ); productRefGroup = {uid('group:Products')}; projectDirPath = ""; projectRoot = ""; targets = ({app_target}, {unit_target}, {ui_target}, ); }};
/* End PBXProject section */

/* Begin PBXResourcesBuildPhase section */
\t\t{app_resources} /* Resources */ = {{isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = ({uid('resource-build:Models')}, {uid('resource-build:Assets')}, ); runOnlyForDeploymentPostprocessing = 0; }};
/* End PBXResourcesBuildPhase section */

/* Begin PBXSourcesBuildPhase section */
\t\t{app_sources} /* Sources */ = {{isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = {list_block(app_build_ids)}; runOnlyForDeploymentPostprocessing = 0; }};
\t\t{unit_sources} /* Sources */ = {{isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = {list_block(unit_build_ids)}; runOnlyForDeploymentPostprocessing = 0; }};
\t\t{ui_sources} /* Sources */ = {{isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = {list_block(ui_build_ids)}; runOnlyForDeploymentPostprocessing = 0; }};
/* End PBXSourcesBuildPhase section */

/* Begin PBXTargetDependency section */
\t\t{target_dependency} /* PBXTargetDependency */ = {{isa = PBXTargetDependency; target = {app_target}; targetProxy = {target_proxy}; }};
\t\t{app_product_dep} /* PBXTargetDependency */ = {{isa = PBXTargetDependency; target = {app_target}; targetProxy = {uid('unit-app-host-proxy')}; }};
/* End PBXTargetDependency section */

/* Begin PBXContainerItemProxy section */
\t\t{uid('unit-app-host-proxy')} /* PBXContainerItemProxy */ = {{isa = PBXContainerItemProxy; containerPortal = {project_id}; proxyType = 1; remoteGlobalIDString = {app_target}; remoteInfo = Reflex; }};
/* End PBXContainerItemProxy section */

/* Begin XCBuildConfiguration section */
\t\t{uid('project-debug')} /* Debug */ = {{isa = XCBuildConfiguration; buildSettings = {{ALWAYS_SEARCH_USER_PATHS = NO; CLANG_ENABLE_MODULES = YES; CLANG_ENABLE_OBJC_ARC = YES; CLANG_WARN_BOOL_CONVERSION = YES; CLANG_WARN_CONSTANT_CONVERSION = YES; CLANG_WARN_DIRECT_OBJC_ISA_USAGE = YES_ERROR; CLANG_WARN_DOCUMENTATION_COMMENTS = YES; CLANG_WARN_EMPTY_BODY = YES; CLANG_WARN_ENUM_CONVERSION = YES; CLANG_WARN_INFINITE_RECURSION = YES; CLANG_WARN_INT_CONVERSION = YES; CLANG_WARN_UNGUARDED_AVAILABILITY = YES_AGGRESSIVE; ENABLE_TESTABILITY = YES; GCC_C_LANGUAGE_STANDARD = gnu17; GCC_NO_COMMON_BLOCKS = YES; GCC_WARN_UNINITIALIZED_AUTOS = YES_AGGRESSIVE; IPHONEOS_DEPLOYMENT_TARGET = 17.0; ONLY_ACTIVE_ARCH = YES; SDKROOT = iphoneos; SWIFT_ACTIVE_COMPILATION_CONDITIONS = DEBUG; SWIFT_OPTIMIZATION_LEVEL = "-Onone"; SWIFT_VERSION = 5.0; }}; name = Debug; }};
\t\t{uid('project-release')} /* Release */ = {{isa = XCBuildConfiguration; buildSettings = {{ALWAYS_SEARCH_USER_PATHS = NO; CLANG_ENABLE_MODULES = YES; CLANG_ENABLE_OBJC_ARC = YES; CLANG_WARN_BOOL_CONVERSION = YES; CLANG_WARN_CONSTANT_CONVERSION = YES; CLANG_WARN_DIRECT_OBJC_ISA_USAGE = YES_ERROR; CLANG_WARN_DOCUMENTATION_COMMENTS = YES; CLANG_WARN_EMPTY_BODY = YES; CLANG_WARN_ENUM_CONVERSION = YES; CLANG_WARN_INFINITE_RECURSION = YES; CLANG_WARN_INT_CONVERSION = YES; CLANG_WARN_UNGUARDED_AVAILABILITY = YES_AGGRESSIVE; GCC_C_LANGUAGE_STANDARD = gnu17; GCC_NO_COMMON_BLOCKS = YES; IPHONEOS_DEPLOYMENT_TARGET = 17.0; SDKROOT = iphoneos; SWIFT_VERSION = 5.0; }}; name = Release; }};

\t\t{uid('app-debug')} /* Debug */ = {{isa = XCBuildConfiguration; baseConfigurationReference = {uid('config:Debug.xcconfig')}; buildSettings = {{CODE_SIGN_STYLE = Automatic; CURRENT_PROJECT_VERSION = 1; ENABLE_TESTABILITY = YES; GENERATE_INFOPLIST_FILE = NO; INFOPLIST_FILE = App/Info.plist; MARKETING_VERSION = 1.0; ONLY_ACTIVE_ARCH = YES; PRODUCT_BUNDLE_IDENTIFIER = "$(REFLEX_BUNDLE_ID)"; PRODUCT_NAME = "$(TARGET_NAME)"; SWIFT_EMIT_LOC_STRINGS = YES; SWIFT_OPTIMIZATION_LEVEL = "-Onone"; SWIFT_VERSION = 5.0; TARGETED_DEVICE_FAMILY = "1"; }}; name = Debug; }};
\t\t{uid('app-release')} /* Release */ = {{isa = XCBuildConfiguration; baseConfigurationReference = {uid('config:Release.xcconfig')}; buildSettings = {{CODE_SIGN_STYLE = Automatic; CURRENT_PROJECT_VERSION = 1; GENERATE_INFOPLIST_FILE = NO; INFOPLIST_FILE = App/Info.plist; MARKETING_VERSION = 1.0; PRODUCT_BUNDLE_IDENTIFIER = "$(REFLEX_BUNDLE_ID)"; PRODUCT_NAME = "$(TARGET_NAME)"; SWIFT_EMIT_LOC_STRINGS = YES; SWIFT_VERSION = 5.0; TARGETED_DEVICE_FAMILY = "1"; }}; name = Release; }};
\t\t{uid('unit-debug')} /* Debug */ = {{isa = XCBuildConfiguration; baseConfigurationReference = {uid('config:Debug.xcconfig')}; buildSettings = {{BUNDLE_LOADER = "$(TEST_HOST)"; CODE_SIGN_STYLE = Automatic; ENABLE_TESTABILITY = YES; GENERATE_INFOPLIST_FILE = YES; ONLY_ACTIVE_ARCH = YES; PRODUCT_BUNDLE_IDENTIFIER = "$(REFLEX_BUNDLE_ID).tests"; PRODUCT_NAME = "$(TARGET_NAME)"; SWIFT_OPTIMIZATION_LEVEL = "-Onone"; SWIFT_VERSION = 5.0; TARGETED_DEVICE_FAMILY = "1"; TEST_HOST = "$(BUILT_PRODUCTS_DIR)/Reflex.app/Reflex"; }}; name = Debug; }};
\t\t{uid('unit-release')} /* Release */ = {{isa = XCBuildConfiguration; baseConfigurationReference = {uid('config:Release.xcconfig')}; buildSettings = {{BUNDLE_LOADER = "$(TEST_HOST)"; CODE_SIGN_STYLE = Automatic; GENERATE_INFOPLIST_FILE = YES; PRODUCT_BUNDLE_IDENTIFIER = "$(REFLEX_BUNDLE_ID).tests"; PRODUCT_NAME = "$(TARGET_NAME)"; SWIFT_VERSION = 5.0; TARGETED_DEVICE_FAMILY = "1"; TEST_HOST = "$(BUILT_PRODUCTS_DIR)/Reflex.app/Reflex"; }}; name = Release; }};
\t\t{uid('ui-debug')} /* Debug */ = {{isa = XCBuildConfiguration; baseConfigurationReference = {uid('config:Debug.xcconfig')}; buildSettings = {{CODE_SIGN_STYLE = Automatic; ENABLE_TESTABILITY = YES; GENERATE_INFOPLIST_FILE = YES; ONLY_ACTIVE_ARCH = YES; PRODUCT_BUNDLE_IDENTIFIER = "$(REFLEX_BUNDLE_ID).uitests"; PRODUCT_NAME = "$(TARGET_NAME)"; SWIFT_OPTIMIZATION_LEVEL = "-Onone"; SWIFT_VERSION = 5.0; TARGETED_DEVICE_FAMILY = "1"; TEST_TARGET_NAME = Reflex; }}; name = Debug; }};
\t\t{uid('ui-release')} /* Release */ = {{isa = XCBuildConfiguration; baseConfigurationReference = {uid('config:Release.xcconfig')}; buildSettings = {{CODE_SIGN_STYLE = Automatic; GENERATE_INFOPLIST_FILE = YES; PRODUCT_BUNDLE_IDENTIFIER = "$(REFLEX_BUNDLE_ID).uitests"; PRODUCT_NAME = "$(TARGET_NAME)"; SWIFT_VERSION = 5.0; TARGETED_DEVICE_FAMILY = "1"; TEST_TARGET_NAME = Reflex; }}; name = Release; }};
/* End XCBuildConfiguration section */

/* Begin XCConfigurationList section */
\t\t{project_config_list} /* Build configuration list for PBXProject */ = {{isa = XCConfigurationList; buildConfigurations = ({uid('project-debug')}, {uid('project-release')}, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; }};
\t\t{app_config_list} /* Build configuration list for PBXNativeTarget "Reflex" */ = {{isa = XCConfigurationList; buildConfigurations = ({uid('app-debug')}, {uid('app-release')}, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; }};
\t\t{unit_config_list} /* Build configuration list for PBXNativeTarget "ReflexAppTests" */ = {{isa = XCConfigurationList; buildConfigurations = ({uid('unit-debug')}, {uid('unit-release')}, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; }};
\t\t{ui_config_list} /* Build configuration list for PBXNativeTarget "ReflexUITests" */ = {{isa = XCConfigurationList; buildConfigurations = ({uid('ui-debug')}, {uid('ui-release')}, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; }};
/* End XCConfigurationList section */

/* Begin XCRemoteSwiftPackageReference section */
\t\t{package_keychain} /* SimpleKeychain */ = {{isa = XCRemoteSwiftPackageReference; repositoryURL = "https://github.com/auth0/SimpleKeychain.git"; requirement = {{kind = exactVersion; version = 1.3.0; }}; }};
\t\t{package_auth0} /* Auth0 */ = {{isa = XCRemoteSwiftPackageReference; repositoryURL = "https://github.com/auth0/Auth0.swift.git"; requirement = {{kind = exactVersion; version = 2.25.0; }}; }};
\t\t{package_livekit} /* LiveKit */ = {{isa = XCRemoteSwiftPackageReference; repositoryURL = "https://github.com/livekit/client-sdk-swift.git"; requirement = {{kind = exactVersion; version = 2.18.0; }}; }};
/* End XCRemoteSwiftPackageReference section */

/* Begin XCLocalSwiftPackageReference section */
\t\t{package_guidance} /* Guidance */ = {{isa = XCLocalSwiftPackageReference; relativePath = Packages/Guidance; }};
\t\t{package_interpretation} /* Targets */ = {{isa = XCLocalSwiftPackageReference; relativePath = Packages/Targets; }};
\t\t{package_tasks} /* XCLocalSwiftPackageReference "Packages/Tasks" */ = {{isa = XCLocalSwiftPackageReference; relativePath = Packages/Tasks; }};
\t\t{package_haptics} /* XCLocalSwiftPackageReference "Packages/HapticLink" */ = {{isa = XCLocalSwiftPackageReference; relativePath = Packages/HapticLink; }};
/* End XCLocalSwiftPackageReference section */

/* Begin XCSwiftPackageProductDependency section */
{package_targets}/* End XCSwiftPackageProductDependency section */

\t}};
\trootObject = {project_id} /* Project object */;
}}
'''

    # PBXBuildFile package entries need IDs matching their PBXBuildFile objects.
    # Keep these references deterministic and repair the source grouping paths.
    PROJECT.parent.mkdir(parents=True, exist_ok=True)
    PROJECT.write_text(text, encoding="utf-8")
    print(f"Wrote {PROJECT.relative_to(IOS.parent)} with {len(app_files)} app, {len(unit_files)} unit-test, and {len(ui_files)} UI-test Swift files.")


if __name__ == "__main__":
    main()
