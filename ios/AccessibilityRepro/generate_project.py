#!/usr/bin/env python3
"""Generate the isolated two-target reproduction using only the Python stdlib."""
from pathlib import Path
import hashlib
root = Path(__file__).resolve().parent
objects = []
def uid(s): return hashlib.sha1(s.encode()).hexdigest()[:24].upper()
def obj(key, value):
    objects.append(f'{uid(key)} = {{ {value} }};')
    return uid(key)
def refs(values): return '(' + ','.join(values) + ',)'
files = []
for path in ('App/ReproApp.swift', 'UITests/ReproUITests.swift'):
    files.append(obj(path, f'isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "{path}"; sourceTree = SOURCE_ROOT;'))
app_product = obj('app-product', 'isa = PBXFileReference; explicitFileType = wrapper.application; path = Repro.app; sourceTree = BUILT_PRODUCTS_DIR;')
test_product = obj('test-product', 'isa = PBXFileReference; explicitFileType = wrapper.cfbundle; path = ReproUITests.xctest; sourceTree = BUILT_PRODUCTS_DIR;')
products = obj('products', f'isa = PBXGroup; children = {refs([app_product, test_product])}; name = Products; sourceTree = "<group>";')
group = obj('main', f'isa = PBXGroup; children = {refs(files + [products])}; sourceTree = "<group>";')
base = 'SDKROOT = iphoneos; IPHONEOS_DEPLOYMENT_TARGET = 17.0; SWIFT_VERSION = 5.0; TARGETED_DEVICE_FAMILY = 1; CODE_SIGNING_ALLOWED = NO; GENERATE_INFOPLIST_FILE = YES; SWIFT_OPTIMIZATION_LEVEL = "-Onone";'
def configs(key, settings):
    ids = [obj(key+mode, f'isa = XCBuildConfiguration; buildSettings = {{ {base} {settings} }}; name = {mode};') for mode in ('Debug', 'Release')]
    return obj(key+'-list', f'isa = XCConfigurationList; buildConfigurations = {refs(ids)}; defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug;')
project_configs = configs('project', 'CLANG_ENABLE_MODULES = YES;')
proxy = obj('proxy', f'isa = PBXContainerItemProxy; containerPortal = {uid("project")}; proxyType = 1; remoteGlobalIDString = {uid("app")}; remoteInfo = Repro;')
dep = obj('dep', f'isa = PBXTargetDependency; target = {uid("app")}; targetProxy = {proxy};')
for key, name, file, product, product_type, settings, deps in [
    ('app', 'Repro', files[0], app_product, 'application', 'PRODUCT_BUNDLE_IDENTIFIER = org.reflex.AccessibilityRepro; INFOPLIST_KEY_UILaunchScreen_Generation = YES; INFOPLIST_KEY_UIApplicationSceneManifest_Generation = YES;', []),
    ('test', 'ReproUITests', files[1], test_product, 'bundle.ui-testing', 'PRODUCT_BUNDLE_IDENTIFIER = org.reflex.AccessibilityReproUITests; TEST_TARGET_NAME = Repro;', [dep]),
]:
    build = obj(key+'-build', f'isa = PBXBuildFile; fileRef = {file};')
    sources = obj(key+'-sources', f'isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = {refs([build])}; runOnlyForDeploymentPostprocessing = 0;')
    conf = configs(key, settings + ' PRODUCT_NAME = "$(TARGET_NAME)";')
    obj(key, f'isa = PBXNativeTarget; buildConfigurationList = {conf}; buildPhases = {refs([sources])}; buildRules = (); dependencies = {refs(deps) if deps else "()"}; name = {name}; productName = {name}; productReference = {product}; productType = "com.apple.product-type.{product_type}";')
obj('project', f'isa = PBXProject; attributes = {{LastUpgradeCheck = 2660; TargetAttributes = {{ {uid("test")} = {{ TestTargetID = {uid("app")}; }}; }}; }}; buildConfigurationList = {project_configs}; compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en, Base); mainGroup = {group}; productRefGroup = {products}; projectDirPath = ""; projectRoot = ""; targets = {refs([uid("app"), uid("test")])};')
project = root/'Repro.xcodeproj'
project.mkdir(exist_ok=True)
(project/'project.pbxproj').write_text('// !$*UTF8*$!\n{ archiveVersion = 1; classes = {}; objectVersion = 56; objects = {\n'+'\n'.join(objects)+f'\n}}; rootObject = {uid("project")}; }}\n')
scheme_dir = project/'xcshareddata/xcschemes'
scheme_dir.mkdir(parents=True, exist_ok=True)
def buildable(key, name, product): return f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{uid(key)}" BuildableName="{product}" BlueprintName="{name}" ReferencedContainer="container:Repro.xcodeproj"/>'
(scheme_dir/'Repro.xcscheme').write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="2660" version="1.3">
<BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{buildable('app','Repro','Repro.app')}</BuildActionEntry></BuildActionEntries></BuildAction>
<TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"><Testables><TestableReference skipped="NO">{buildable('test','ReproUITests','ReproUITests.xctest')}</TestableReference></Testables></TestAction>
<LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0">{buildable('app','Repro','Repro.app')}</BuildableProductRunnable></LaunchAction>
</Scheme>''')
