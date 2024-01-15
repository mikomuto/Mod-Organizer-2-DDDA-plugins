# This Mod Organizer plugin is released to the pubic under the terms of the GNU GPL version 3, which is accessible from the Free Software Foundation here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

# To use this plugin, place it in the plugins directory of your Mod Organizer install. You will then find a 'Run ARCTool' option under the tools menu.

# Intended behaviour:
# * Adds button to tools menu.
# * If ARCTool' location isn't known (or isn't valid, e.g. ARCTool isn't actually there) when the button is pressed, a file chooser is displayed to find ARCTool.
# asks user for 

import os
import shutil
import pathlib
from glob import glob
from PyQt6.QtCore import QCoreApplication, qCritical, QFileInfo, qInfo, QThreadPool, QRunnable, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QFileSystemModel
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog

if "mobase" not in sys.modules:
    import mock_mobase as mobase

class ARCToolInvalidPathException(Exception):
    """Thrown if ARCTool.exe path can't be found"""
    pass

class ARCToolMissingException(Exception):
    """Thrown if selected ARC file can't be found"""
    pass

class ARCToolInactiveException(Exception):
    """Thrown if ARCTool.exe is installed to an inactive mod"""
    pass

class ARCFileMissingException(Exception):
    """Thrown if selected ARC file can't be found"""
    pass

class MergeMod(mobase.IPluginTool):

    def __init__(self):
        super(MergeMod, self).__init__()
        self._organizer = None
        self._parentWidget = None

    def init(self, organizer):
        self._organizer = organizer
        self.threadpool = QThreadPool()
        return True
        
    def name(self):
        return "DDDA GOG Tool"

    def localizedName(self):
        return self.__tr("DDDA GOG Tool")

    def author(self):
        return "MikoMuto"

    def description(self):
        return self.__tr("Runs ARCTool on a mod to extract all arc files and remove all zht.gmd language files")

    def version(self):
        return mobase.VersionInfo(1, 0, 0, 0)

    def requirements(self):
        return [
            mobase.PluginRequirementFactory.gameDependency("Dragon's Dogma: Dark Arisen")
        ]

    def isActive(self) -> bool:
        return self._organizer.pluginSetting(self.name(), "enabled")

    def settings(self):
        return [
        mobase.PluginSetting("enabled", "enable this plugin", True),
        mobase.PluginSetting("ARCTool-path", self.__tr("Path to ARCTool.exe"), ""),
        mobase.PluginSetting("initialised", self.__tr("Settings have been initialised.  Set to False to reinitialise them."), False),
        mobase.PluginSetting("log-enabled", self.__tr("Enable logs"), False),        
        mobase.PluginSetting("verbose-log", self.__tr("Verbose logs"), False),
            ]

    def displayName(self):
        return self.__tr("DDDA GOG Tool")

    def tooltip(self):
        return self.__tr("Removes all zht.gmd files from archives")

    def icon(self):
        ARCToolPath = self._organizer.pluginSetting(self.name(), "ARCTool-path")
        if os.path.exists(ARCToolPath):
            # We can't directly grab the icon from an executable, but this seems like the simplest alternative.
            fin = QFileInfo(ARCToolPath)
            model = QFileSystemModel()
            model.setRootPath(fin.path())
            return model.fileIcon(model.index(fin.filePath()))
        else:
            # Fall back to where the user might have put an icon manually.
            return QIcon("plugins/ARCTool.ico")

    def setParentWidget(self, widget):
        self._parentWidget = widget

    def display(self):
        args = []

        if not bool(self._organizer.pluginSetting(self.name(), "initialised")):
            self._organizer.setPluginSetting(self.name(), "ARCTool-path", "")

        try:
            executable = self.getARCToolPath()
        except ARCToolInvalidPathException:
            QMessageBox.critical(self._parentWidget, self.__tr("ARCTool path not specified"), self.__tr("The path to ARCTool.exe wasn't specified. The tool will now exit."))
            return
        except ARCToolMissingException:
            QMessageBox.critical(self._parentWidget, self.__tr("ARCTool not found"), self.__tr("ARCTool.exe not found. Resetting tool."))
            return
        except ARCToolInactiveException:
            # Error has already been displayed, just quit
            return

        self._organizer.setPluginSetting(self.name(), "initialised", True)
        
        try:
            path = self.__getModFolderPath()
        except ARCFileMissingException:
            QMessageBox.critical(self._parentWidget, self.__tr("Mod folder not specified"), self.__tr("A valid folder was not specified. This tool will now exit."))
            return
            
        compressResult = self.processMod(executable, path)

    def __tr(self, str):
        return QCoreApplication.translate("ARCTool", str)
        
    def __getModFolderPath(self):
        modDirectory = self.__getModDirectory()
        gameDataDirectory = self._organizer.managedGame().dataDirectory().absolutePath()
        path = QFileDialog.getExistingDirectory(self._parentWidget, self.__tr("Locate mod to clean"), str(modDirectory))
        if path == "":
        # Cancel was pressed
            raise ARCFileMissingException
        return path
        
    def getARCToolPath(self):
        savedPath = self._organizer.pluginSetting(self.name(), "ARCTool-path")
        # ARCTool must be installed within the game's data directory or a mod folder
        modDirectory = self.__getModDirectory()
        gameDataDirectory = pathlib.Path(self._organizer.managedGame().dataDirectory().absolutePath())
        pathlibPath = pathlib.Path(savedPath)
        if not os.path.exists(pathlibPath):
            self._organizer.setPluginSetting(self.name(), "ARCTool-path", "")
            self._organizer.setPluginSetting(self.name(), "initialised", False)
            raise ARCToolMissingException
        inGoodLocation = self.__withinDirectory(pathlibPath, modDirectory)
        inGoodLocation |= self.__withinDirectory(pathlibPath, gameDataDirectory)
        if not pathlibPath.is_file() or not inGoodLocation:
            QMessageBox.information(self._parentWidget, self.__tr("ARCTool not found"), self.__tr("ARCTool path invalid or not set. \n\nARCTool must be visible within the VFS, choose an installation either within a mod folder. \n\nThis setting can be updated in the Plugins tab of the Mod Organizer Settings menu."))
            while True:
                path = QFileDialog.getOpenFileName(self._parentWidget, self.__tr("Locate ARCTool.exe"), str(modDirectory), "ARCTool.exe")[0]
                if path == "":
                    # Cancel was pressed
                    raise ARCToolInvalidPathException
                pathlibPath = pathlib.Path(path)
                inGoodLocation = self.__withinDirectory(pathlibPath, modDirectory)
                inGoodLocation |= self.__withinDirectory(pathlibPath, gameDataDirectory)
                if pathlibPath.is_file() and inGoodLocation:
                    self._organizer.setPluginSetting(self.name(), "ARCTool-path", path)
                    savedPath = path
                    break
                else:
                    QMessageBox.information(self._parentWidget, self.__tr("Not a compatible location..."), self.__tr("ARCTool only works when within the VFS, so must be installed within a mod folder. Please select a different ARC installation"))
        # Check the mod is actually enabled
        if self.__withinDirectory(pathlibPath, modDirectory):
            ARCModName = None
            for path in pathlibPath.parents:
                if path.parent.samefile(modDirectory):
                    ARCModName = path.name
                    break
            if (self._organizer.modList().state(ARCModName) & mobase.ModState.active) == 0:
                # ARC is installed to an inactive mod
                result = QMessageBox.question(self._parentWidget, self.__tr("ARCTool mod deactivated"), self.__tr("ARCTool is installed to an inactive mod. /n/nPress Yes to activate it or Cancel to quit the tool"), QMessageBox.StandardButton(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel))
                if result == QMessageBox.StandardButton.Yes:
                    self._organizer.modList().setActive(ARCModName, True)
                else:
                    raise ARCToolInactiveException
        return savedPath    
        
    def processMod(self, executable, path):
        # initialise progress dialog
        self.myProgressD = QProgressDialog(self.__tr("DDDA GOG Tool"), None, 0, 0, self._parentWidget)
        self.myProgressD.forceShow()   
        self.myProgressD.setMaximum(0)
        
        # start single scan thread
        worker = scanThreadWorker(self._organizer, executable, path)
        worker.signals.log_out.connect(self.scanThreadWorkerProgress)
        worker.signals.finished.connect(self.scanThreadWorkerComplete)
        # Execute
        self.threadpool.start(worker)
        
        return True
        
    def scanThreadWorkerProgress(self, log_out):
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            qInfo(log_out)
        
    def scanThreadWorkerComplete(self):
        self.myProgressD.hide()
        QMessageBox.information(self._parentWidget, "DDDA GOG Tool", self.__tr(f'Process complete'))

    def __getModDirectory(self):
        return self._organizer.modsPath()

    @staticmethod
    def __withinDirectory(innerPath, outerDir):
        for path in innerPath.parents:
            if path.samefile(outerDir):
                return True
        return False
        
class scanThreadWorkerSignals(QObject):
    finished = pyqtSignal()
    log_out = pyqtSignal(str)

class scanThreadWorker(QRunnable):
    def __init__(self, organizer, executable, path):
        self._organizer = organizer
        self._executable = executable
        self._path = path
        self.signals = scanThreadWorkerSignals()
        super(scanThreadWorker, self).__init__()

    @pyqtSlot()
    def run(self):
        allArcFiles = [y for x in os.walk(self._path) for y in glob(os.path.join(x[0], '*.arc'))]
        # Unpack everything
        for arcFile in allArcFiles:
            path = self._executable + " -dd -texRE6 -alwayscomp -pc -txt -v 7  \"" + arcFile + "\""
            self.signals.log_out.emit(f'Extracting {arcFile}') 
            out = os.popen(path).read()
            if bool(self._organizer.pluginSetting("DDDA GOG Tool", "verbose-log")):
                self.signals.log_out.emit(out) 
        # Delete zht.gmd
        allGmdFiles = [y for x in os.walk(os.getcwd()) for y in glob(os.path.join(x[0], '*zht.gmd'))]
        for gmdFile in allGmdFiles:
            self.signals.log_out.emit(f'Deleting {gmdFile}')
            os.remove(gmdFile)
        # Repack everything
        for arcFile in allArcFiles:
            arcFolder = os.path.splitext(arcFile)[0]
            path =  self._executable + " -dd -texRE6 -alwayscomp -pc -txt -v 7  \"" + arcFolder + "\""
            self.signals.log_out.emit(f'Compressing {arcFolder}')
            out = os.popen(path).read()
            if bool(self._organizer.pluginSetting("DDDA GOG Tool", "verbose-log")):
                self.signals.log_out.emit(out) 
            # delete unpacked folder
            shutil.rmtree(arcFolder)
            # remove .arc.txt
            pathlib.Path(f'{arcFolder}.arc.txt').unlink(missing_ok=True)
        self.signals.finished.emit()  # Done
        return
            
def createPlugin():
    return MergeMod()
