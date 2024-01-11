# This Mod Organizer plugin is released to the pubic under the terms of the GNU GPL version 3, which is accessible from the Free Software Foundation here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

# To use this plugin, place it in the plugins directory of your Mod Organizer install. You will then find a 'ARC Extract' option under the tools menu.

# Intended behaviour:
# * Adds button to tools menu.
# * If ARCTool' location isn't known (or isn't valid, e.g. ARCTool isn't actually there) when the button is pressed, a file chooser is displayed to find ARCTool.
# scans all enabled mods for duplicate .arc files, copies vanilla arc from game folder to a temp folder, extracts this arc in all actve mods, deletes identical to master files, then exits

import os
import re
import shutil
import pathlib
import sys
import filecmp
from collections import defaultdict

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

class ARCExtract(mobase.IPluginTool):
    arcFilesSeenDict = defaultdict(list)
    duplicateARCFileDict = defaultdict(list)
    threadCancel = False

    def __init__(self):
        super(ARCExtract, self).__init__()
        self._organizer = None
        self.__parentWidget = None

    def init(self, organizer):
        self._organizer = organizer
        self.threadpool = QThreadPool()
        self.currentIndex = 0
        self.myProgressD = None
        return True

    def name(self):
        return "ARC Extract"

    def localizedName(self):
        return self.__tr("ARC Extract")

    def author(self):
        return "MikoMuto"

    def description(self):
        return self.__tr("Runs ARCTool on mods to extract files")

    def version(self):
        return mobase.VersionInfo(2, 0, 0, 0)

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
        mobase.PluginSetting("remove-ITM", self.__tr("Remove identical to master files when extracting ARC files"), True),
        mobase.PluginSetting("delete-ARC", self.__tr("Delete .arc file after extracting"), True),
        mobase.PluginSetting("log-enabled", self.__tr("Enable logs"), False),
        mobase.PluginSetting("verbose-log", self.__tr("Verbose logs"), False),
            ]

    def displayName(self):
        return self.__tr("ARC Extract")

    def tooltip(self):
        return self.__tr("Unpacks all ARC files")

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
        self.__parentWidget = widget

    def display(self):
        if not bool(self._organizer.pluginSetting(self.name(), "initialised")):
            self._organizer.setPluginSetting(self.name(), "ARCTool-path", "")
        try:
            executable = self.get_arctool_path()
        except ARCToolInvalidPathException:
            QMessageBox.critical(self.__parentWidget, self.__tr("ARCTool path not specified"), self.__tr("The path to ARCTool.exe wasn't specified. The tool will now exit."))
            return
        except ARCToolMissingException:
            QMessageBox.critical(self.__parentWidget, self.__tr("ARCTool not found"), self.__tr("ARCTool.exe not found. Resetting tool."))
            return
        except ARCToolInactiveException:
            # Error has already been displayed, just quit
            return

         # reset cancelled flag
        ARCExtract.threadCancel = False
        self._organizer.setPluginSetting(self.name(), "initialised", True)
        self.processMods(executable)

    def __tr(self, str):
        return QCoreApplication.translate("ARCTool", str)

    def get_arctool_path(self):
        savedPath = self._organizer.pluginSetting(self.name(), "ARCTool-path")
        # ARCTool must be installed within the game's data directory or a mod folder
        mod_directory = self._organizer.modsPath()
        game_directory = pathlib.Path(self._organizer.managedGame().dataDirectory().absolutePath())
        pathlibPath = pathlib.Path(savedPath)
        if not os.path.exists(pathlibPath):
            self._organizer.setPluginSetting(self.name(), "ARCTool-path", "")
            self._organizer.setPluginSetting(self.name(), "initialised", False)
            raise ARCToolMissingException
        inGoodLocation = self.__withinDirectory(pathlibPath, mod_directory)
        inGoodLocation |= self.__withinDirectory(pathlibPath, game_directory)
        if not pathlibPath.is_file() or not inGoodLocation:
            QMessageBox.information(self.__parentWidget, self.__tr("ARCTool not found"), self.__tr("ARCTool path invalid or not set. \n\nARCTool must be visible within the VFS, choose an installation within a mod folder. \n\nThis setting can be updated in the Plugins tab of the Mod Organizer Settings menu."))
            while True:
                path = QFileDialog.getOpenFileName(self.__parentWidget, self.__tr("Locate ARCTool.exe"), str(mod_directory), "ARCTool.exe")[0]
                if path == "":
                    # Cancel was pressed
                    raise ARCToolInvalidPathException
                pathlibPath = pathlib.Path(path)
                inGoodLocation = self.__withinDirectory(pathlibPath, mod_directory)
                inGoodLocation |= self.__withinDirectory(pathlibPath, game_directory)
                if pathlibPath.is_file() and inGoodLocation:
                    self._organizer.setPluginSetting(self.name(), "ARCTool-path", path)
                    savedPath = path
                    break
                else:
                    QMessageBox.information(self.__parentWidget, self.__tr("Not a compatible location..."), self.__tr("ARCTool only works when within the VFS, so must be installed within a mod folder. Please select a different ARC installation"))
        # Check the mod is actually enabled
        if self.__withinDirectory(pathlibPath, mod_directory):
            ARCModName = None
            for path in pathlibPath.parents:
                if path.parent.samefile(mod_directory):
                    ARCModName = path.name
                    break
            if (self._organizer.modList().state(ARCModName) & mobase.ModState.active) == 0:
                # ARC is installed to an inactive mod
                result = QMessageBox.question(self.__parentWidget, self.__tr("ARCTool mod deactivated"), self.__tr("ARCTool is installed to an inactive mod. Press Yes to activate it or Cancel to quit the tool"), QMessageBox.StandardButton(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel))
                if result == QMessageBox.StandardButton.Yes:
                    self._organizer.modList().setActive(ARCModName, True)
                else:
                    raise ARCToolInactiveException
        return savedPath

    def processMods(self, executable): # called from display()
        self.arcFilesSeenDict.clear()
        self.duplicateARCFileDict.clear()
        mod_directory = self._organizer.modsPath()
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        executablePath, executableName = os.path.split(executable)
        arctool_mod = os.path.relpath(executablePath, mod_directory).split(os.path.sep, 1)[0]

        # get mod active list
        modActiveList = []
        modlist = self._organizer.modList()
        for mod_name in modlist.allModsByProfilePriority():
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if mod_name != arctool_mod and 'Merged ARC' not in mod_name:
                    modActiveList.append(mod_name)

        # initialise progress dialog
        self.myProgressD = QProgressDialog(self.__tr("ARC Extraction"), self.__tr("Cancel"), 0, 0, self.__parentWidget)
        self.myProgressD.forceShow()
        self.myProgressD.setFixedWidth(300)

        # set mod count for progress
        self.myProgressD.setLabelText(self.__tr("Scanning..."))
        self.myProgressD.setMaximum(len(modActiveList))
        currentIndex = 0

        # start single scan thread
        worker = scanThreadWorker(self._organizer, modActiveList)
        worker.signals.progress.connect(self.scanThreadWorkerProgress)
        worker.signals.result.connect(self.scanThreadWorkerOutput)
        worker.signals.finished.connect(self.scanThreadWorkerComplete)
        # Execute
        self.threadpool.start(worker)

    def scanThreadWorkerProgress(self, progress): # called after each mod is scanned in scanThreadWorker()
        if (self.myProgressD.wasCanceled()):
            ARCExtract.threadCancel = True
        else:
            self.myProgressD.setValue(progress)

    def scanThreadWorkerComplete(self): # called after completion of scanThreadWorker()
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            qInfo(f'Scan complete')
            qInfo(f'Duplicate ARC count: {len(self.duplicateARCFileDict)}')
            qInfo(f'Unique ARC count: {len(self.arcFilesSeenDict)}')
        # start extraction
        self.extractDuplicateARCs()

    def scanThreadWorkerOutput(self, log_out):
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            qInfo(log_out)

    def extractDuplicateARCs(self): # called after completion of buildDuplicatesDictionary()
        # set file count for progress
        self.myProgressD.setValue(0)
        self.myProgressD.setMaximum(len(self.duplicateARCFileDict))
        self.myProgressD.setLabelText(self.__tr("Extracting..."))
        self.currentIndex = 0
        # extract based on duplicates found
        for arcFile in self.duplicateARCFileDict:
            mod_list = self.duplicateARCFileDict[arcFile]
            # Pass the function to execute
            worker = extractThreadWorker(self._organizer, mod_list, arcFile)
            worker.signals.result.connect(self.extractThreadWorkerOutput)
            worker.signals.finished.connect(self.extractThreadWorkerComplete)
            # Execute
            self.threadpool.start(worker)

    def extractThreadCleanup(self): # called after completion of all extractThreadWorker()
        merge_mod = 'Merged ARC - ' + self._organizer.profileName()
        self.myProgressD.hide()
        QMessageBox.information(self.__parentWidget, self.__tr(""), self.__tr(f'Extraction complete'))
        self._organizer.modList().setActive(merge_mod, True)
        self._organizer.refresh()

    def extractThreadWorkerComplete(self): # called after completion of each extractThreadWorker()
        self.currentIndex += 1
        if self.currentIndex == self.myProgressD.maximum():
            self.extractThreadCleanup()
        if (self.myProgressD.wasCanceled()):
            ARCExtract.threadCancel = True
            self.extractThreadCleanup()
        else:
            self.myProgressD.setValue(self.currentIndex)

    def extractThreadWorkerOutput(self, log_out):
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            qInfo(log_out)

    @staticmethod
    def __withinDirectory(innerPath, outerDir):
        for path in innerPath.parents:
            if path.samefile(outerDir):
                return True
        return False

class scanThreadWorkerSignals(QObject):
    finished = pyqtSignal()
    progress = pyqtSignal(int)
    result = pyqtSignal(str)

class scanThreadWorker(QRunnable):
    def __init__(self, organizer, mod_active_list):
        self._organizer = organizer
        self._mod_active_list = mod_active_list
        self.signals = scanThreadWorkerSignals()
        super(scanThreadWorker, self).__init__()

    @pyqtSlot()
    def run(self):
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        mod_directory = self._organizer.modsPath()
        log_out = "\n"
        mods_scanned = 0
        # build list of active mod duplicate arc files to extract
        for mod_name in self._mod_active_list:
            if ARCExtract.threadCancel:
                return
            mods_scanned += 1
            self.signals.progress.emit(mods_scanned) # update progress
            log_out += f'Scanning: {mod_name}\n'
            for dirpath, dirnames, filenames in os.walk(mod_directory + os.path.sep + mod_name):
                # check for extracted arc folders
                for folder in dirnames:
                    full_path = dirpath + os.path.sep + folder + ".arc"
                    relative_path = os.path.relpath(full_path, mod_directory).split(os.path.sep, 1)[1]
                    if (os.path.isfile(os.path.normpath(game_directory + os.path.sep + relative_path))):
                        if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                            log_out += f'ARC Folder: {full_path}\n'
                        if any(relative_path in x for x in ARCExtract.arcFilesSeenDict):
                            mod_where_first_seen = ARCExtract.arcFilesSeenDict[relative_path][0]
                            ARCExtract.duplicateARCFileDict[relative_path].append(mod_where_first_seen)
                            if mod_name not in ARCExtract.duplicateARCFileDict[relative_path]:
                                ARCExtract.duplicateARCFileDict[relative_path].append(mod_name)
                        else:
                            if mod_name not in ARCExtract.arcFilesSeenDict[relative_path]:
                                ARCExtract.arcFilesSeenDict[relative_path].append(mod_name)
                for file in filenames:
                    if file.endswith(".arc"):
                        full_path = dirpath + os.path.sep + file
                        relative_path = os.path.relpath(full_path, mod_directory).split(os.path.sep, 1)[1]
                        if any(relative_path in x for x in ARCExtract.arcFilesSeenDict):
                            mod_where_first_seen = ARCExtract.arcFilesSeenDict[relative_path][0]
                            ARCExtract.duplicateARCFileDict[relative_path].append(mod_where_first_seen)
                            log_out += f'Duplicate ARC: {os.path.normpath(dirpath + os.sep + file)}\n'
                            if mod_name not in ARCExtract.duplicateARCFileDict[relative_path]:
                                ARCExtract.duplicateARCFileDict[relative_path].append(mod_name)
                        else:
                            #if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                            #log_out += f'Unique ARC: {relative_path}\n'
                            if mod_name not in ARCExtract.arcFilesSeenDict[relative_path]:
                                ARCExtract.arcFilesSeenDict[relative_path].append(mod_name)
        self.signals.result.emit(log_out)  # Return log
        self.signals.finished.emit()  # Done
        return

class extractThreadWorkerSignals(QObject):
    finished = pyqtSignal()
    result = pyqtSignal(str)

class extractThreadWorker(QRunnable):
    def __init__(self, organizer, mod_list, arc_file):
        self._organizer = organizer
        self._mod_list = mod_list
        self._arc_file = arc_file
        self.signals = extractThreadWorkerSignals()
        super(extractThreadWorker, self).__init__()

    @pyqtSlot()
    def run(self):
        # check for cancellation
        if ARCExtract.threadCancel:
                return
        args = "-x -pc -dd -alwayscomp -txt -v 7"
        executable = self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "ARCTool-path")
        executablePath, executableName = os.path.split(executable)
        extracted_arc = os.path.splitext(self._arc_file)[0]
        arc_file_parent = os.path.dirname(self._arc_file)
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        mod_directory = self._organizer.modsPath()
        log_out = "\n"
        # extract vanilla if needed
        extractedARCfolder = pathlib.Path(executablePath + os.sep + extracted_arc)
        if not (os.path.isdir(extractedARCfolder)):
            log_out += f'Extracting vanilla ARC: {self._arc_file}\n'
            if (os.path.isfile(os.path.join(game_directory, self._arc_file))):
                pathlib.Path(executablePath + os.sep +  arc_file_parent).mkdir(parents=True, exist_ok=True)
                shutil.copy(os.path.normpath(os.path.join(game_directory, self._arc_file)), os.path.normpath(executablePath + os.sep + arc_file_parent))
                output = os.popen('"' + executable + '" ' + args + ' "' + os.path.normpath(executablePath + os.sep + self._arc_file + '"')).read()
                # remove .arc file
                os.remove(os.path.normpath(executablePath + os.sep + self._arc_file))
        for mod_name in self._mod_list:            
            if os.path.isfile(mod_directory + os.sep + mod_name + os.sep + self._arc_file):
                log_out += f'Extracting: {mod_name}{os.sep}{self._arc_file}\n'
                # extract arc and remove ITM
                output = os.popen('"' + executable + '" ' + args + ' "' + os.path.normpath(mod_directory + os.sep + mod_name + os.sep + self._arc_file + '"')).read()
                if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                    log_out += "------ start arctool output ------\n"
                    log_out += output + "------ end arctool output ------\n"
                # remove ITM
                if bool(self._organizer.pluginSetting("ARC Extract", "remove-ITM")):
                    log_out += 'Deleting duplicate files\n'
                    def delete_same_files(dcmp):
                        for name in dcmp.same_files:
                            if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                                log_out += f'Deleting duplicate file {os.path.join(dcmp.right, name)}\n'
                            os.remove(os.path.join(dcmp.right, name))
                        for sub_dcmp in dcmp.subdirs.values():
                            delete_same_files(sub_dcmp)
                    dcmp = filecmp.dircmp(executablePath + os.sep + extracted_arc, mod_directory + os.sep + mod_name + os.sep +extracted_arc)
                    delete_same_files(dcmp)
                    # delete empty folders
                    for dirpath, dirnames, filenames in os.walk(extracted_arc, topdown=False):
                        for dirname in dirnames:
                            full_path = os.path.join(dirpath, dirname)
                            if not os.listdir(full_path):
                                if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                                    log_out += f'Deleting empty folder {full_path}\n'
                                os.rmdir(full_path)
                # delete arc
                if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "delete-ARC")):
                    os.remove(mod_directory + os.sep + mod_name + os.sep + self._arc_file)
                log_out += "ARC extract complete"
        if log_out != "\n":
            self.signals.result.emit(log_out) # Return log
        self.signals.finished.emit() # Done
        return

def createPlugin():
    return ARCExtract()
