# This Mod Organizer plugin is released to the pubic under the terms of the
# GNU GPL version 3, which is accessible from the Free Software Foundation
# here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

""" add support for ARCtool """

import os
import json
import filecmp
import logging
import pathlib
import shutil
from collections import defaultdict

from PyQt6.QtCore import (QThreadPool, QRunnable, QObject, pyqtSignal, pyqtSlot, qInfo)
from PyQt6.QtGui import QIcon, QFileSystemModel
from PyQt6.QtWidgets import ( QApplication, QMessageBox, QProgressDialog)

import mobase


class ARCtoolInvalidPathException(Exception):
    """Thrown if ARCtool.exe path can't be found"""


class ARCtoolMissingException(Exception):
    """Thrown if selected ARC tool can't be found"""


class ARCExtract(mobase.IPluginTool):
    arc_files_seen_dict = defaultdict(list)
    arc_files_duplicate_dict = defaultdict(list)
    arc_folders_previous_build_dict = defaultdict(list)

    def __init__(self):
        super(ARCExtract, self).__init__()
        self._organizer = None
        self.threadpool = None
        self.threadcancel = False
        self.current_index = 0
        self.extract_progress_dialog = None
        self.logger = None
        self.__parent_widget = None

    def init(self, organizer):
        self._organizer = organizer
        self.threadpool = QThreadPool()
        self.threadcancel = False
        return True

    def name(self):
        return "ARC Extract"

    def localizedName(self):
        return self.__tr("ARC Extract")

    def author(self):
        return "MikoMuto"

    def description(self):
        return self.__tr("Runs ARCtool on mods to extract files")

    def version(self):
        return mobase.VersionInfo(2, 0, 1)

    def requirements(self):
        return [
            mobase.PluginRequirementFactory.gameDependency(
                "Dragon's Dogma: Dark Arisen"
            )
        ]

    def isActive(self) -> bool:
        return self._organizer.pluginSetting(self.name(), "enabled")

    def settings(self):
        return [
            mobase.PluginSetting("enabled", "enable this plugin", True),
            mobase.PluginSetting(
                "restore default",
                self.__tr(
                    "Set to True to restore default settings."
                ),
                False,
            ),
            mobase.PluginSetting(
                "remove-ITM",
                self.__tr("Remove identical to master files when extracting ARC files"),
                True,
            ),
            mobase.PluginSetting(
                "delete-ARC",
                self.__tr("Delete duplicate mod .arc file after extracting"),
                True,
            ),
            mobase.PluginSetting(
                "log-enabled",
                self.__tr(
                    "Enable logging. Log file can be found in the ARCtool mod folder"
                ),
                False,
            ),
            mobase.PluginSetting(
                "verbose-log", self.__tr("Verbose logs. More info!!!"), False
            ),
            mobase.PluginSetting(
                "uncheck-mods",
                self.__tr(
                    "Uncheck ARCtool mod and mods without valid game data after merge."
                    + " This will speed up game loading"
                ),
                True,
            ),
            mobase.PluginSetting(
                "max-threads", self.__tr("Maximum number of threads to allocate"), 2
            ),
            mobase.PluginSetting(
                "merge-mode", self.__tr("Extract everything and delete ITM"), False
            ),
        ]

    def displayName(self):
        return self.__tr("ARC Extract")

    def tooltip(self):
        return self.__tr("Unpacks ARC files")

    def icon(self):
        return QIcon(":/MO/gui/content/plugin")

    def setParentWidget(self, widget):
        self.__parent_widget = widget

    def display(self):
        # reset settings if needed
        if bool(self._organizer.pluginSetting(self.name(), "restore default")):
            # reset all
            self._organizer.setPluginSetting(self.name(), "remove-ITM", True)
            self._organizer.setPluginSetting(self.name(), "delete-ARC", True)
            self._organizer.setPluginSetting(self.name(), "log-enabled", False)
            self._organizer.setPluginSetting(self.name(), "verbose-log", False)
            self._organizer.setPluginSetting(self.name(), "uncheck-mods", True)
            self._organizer.setPluginSetting(
                self.name(), "max-threads", self.threadpool.maxThreadCount()
            )
            self._organizer.setPluginSetting(self.name(), "merge-mode", False)
        # verify that ARCtool path is still valid
        try:
            executable = self.get_arctool()
        except ARCtoolInvalidPathException:
            QMessageBox.critical(
                self.__parent_widget,
                self.__tr("Incorrect ARCtool path"),
                self.__tr("Invalid path for ARCtool.exe. The tool will now exit."),)
            return
        except ARCtoolMissingException:
            QMessageBox.critical(
                self.__parent_widget,
                self.__tr("ARCtool not found"),
                self.__tr("ARCtool.exe not found. Exiting tool."),
            )
            return
        # logger setup
        if self._organizer.pluginSetting(self.name(), "log-enabled"):
            log_file = self._organizer.overwritePath() + "\\ARCExtract.log"
            self.logger = logging.getLogger("ae_logger")
            f_handler = logging.FileHandler(log_file, "w+")
            f_handler.setLevel(logging.DEBUG)
            f_format = logging.Formatter("%(asctime)s %(message)s")
            f_handler.setFormatter(f_format)
            self.logger.addHandler(f_handler)
            self.logger.propagate = False
        # reset cancelled flag
        ARCExtract.threadCancel = False
        self._organizer.setPluginSetting(self.name(), "restore default", False)
        # check for inactive mods
        if self._organizer.pluginSetting(self.name(), "uncheck-mods"):
            modlist = self._organizer.modList()
            enable_all = False
            skip_all = False
            for mod_name in modlist.allModsByProfilePriority():
                if not modlist.state(mod_name) & mobase.ModState.ACTIVE:
                    if enable_all:
                        self._organizer.modList().setActive(mod_name, True)
                    elif not skip_all:
                        result = self.show_activate_dialog(mod_name)
                        if result == QMessageBox.StandardButton.Yes.value:
                            self._organizer.modList().setActive(mod_name, True)
                        if result == QMessageBox.StandardButton.YesToAll.value:
                            enable_all = True
                            self._organizer.modList().setActive(mod_name, True)
                        if result == QMessageBox.StandardButton.NoToAll.value:
                            skip_all = True
        # run the stuff
        self.process_mods(executable)

    def __tr(self, txt: str) -> str:
        return QApplication.translate("ARCtool", txt)

    def show_activate_dialog(self, mod_name):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(f"Mod {mod_name} is disabled. Do you wish to enable it?")
        msg.setInformativeText(
            "Disabled mods will not be included in the"
            + " extract process and may result in duplicate"
            + " .arc files not being detected."
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.YesToAll
            | QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.NoToAll
        )
        retval = msg.exec()
        return retval

    def get_arctool(self):
        arctool_path = os.path.join(self._organizer.basePath(), "ARCtool.exe")
        if not os.path.isfile(arctool_path):
            raise ARCtoolMissingException

    def process_mods(self, executable):  # called from display()
        self.arc_files_seen_dict.clear()
        self.arc_files_duplicate_dict.clear()

        # warn if merge mode active
        if bool(self._organizer.pluginSetting(self.name(), "merge-mode")):
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Merge Mode Active")
            msg.setText(
                "WARNING: All active mods will be extracted and Identical "
                + "To Master files removed.\n\nDo you wish to continue?"
            )
            msg.setStandardButtons(
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            )
            retval = msg.exec()
            if retval == QMessageBox.StandardButton.No.value:
                return

        # get mod active list
        mod_active_list = []
        modlist = self._organizer.modList()
        for mod_name in modlist.allModsByProfilePriority():
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if "Merged ARC" not in mod_name:
                    mod_active_list.append(mod_name)

        # initialise progress dialog
        self.extract_progress_dialog = QProgressDialog(
            self.__tr("ARC Extraction"), self.__tr("Cancel"), 0, 0, self.__parent_widget
        )
        self.extract_progress_dialog.setFixedWidth(300)

        # set mod count for progress
        self.extract_progress_dialog.setLabelText(self.__tr("Scanning..."))
        self.extract_progress_dialog.setMaximum(len(mod_active_list))
        self.extract_progress_dialog.forceShow()
        self.current_index = 0

        # set max thread count
        self.threadpool.setMaxThreadCount(
            self._organizer.pluginSetting(self.name(), "max-threads")
        )

        # start single scan thread
        worker = ScanThreadWorker(self._organizer, mod_active_list)
        worker.signals.progress.connect(self.scan_thread_worker_progress)
        worker.signals.result.connect(self.scan_thread_worker_output)
        worker.signals.finished.connect(self.scan_thread_worker_complete)
        # Execute
        self.threadpool.start(worker)

    def scan_thread_worker_progress(
        self, progress
    ):  # called after each mod is scanned in ScanThreadWorker()
        if self.extract_progress_dialog.wasCanceled():
            ARCExtract.threadCancel = True
        else:
            self.extract_progress_dialog.setValue(progress)

    def scan_thread_worker_complete(
        self,
    ):  # called after completion of ScanThreadWorker()
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            self.logger.debug("Scan complete")
            self.logger.debug(
                "Duplicate ARC count: %s", len(self.arc_files_duplicate_dict)
            )
            self.logger.debug("Unique ARC count: %s", len(self.arc_files_seen_dict))
        # start extraction
        if len(self.arc_files_duplicate_dict) > 0:
            self.extract_duplicate_arcs()
        else:
            self.extract_progress_dialog.hide()
            QMessageBox.information(
            self.__parent_widget, self.__tr("Scan complete"), self.__tr(
                "Nothing to do"))

    def scan_thread_worker_output(self, log_out):
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            self.logger.debug(log_out)

    def extract_duplicate_arcs(self):
        # set file count for progress
        self.extract_progress_dialog.setValue(0)
        self.extract_progress_dialog.setMaximum(len(self.arc_files_duplicate_dict))
        self.extract_progress_dialog.setLabelText(self.__tr("Extracting..."))
        self.current_index = 0
        # extract based on duplicates found
        for arc_file in self.arc_files_duplicate_dict:
            mod_list = self.arc_files_duplicate_dict[arc_file]
            # Pass the function to execute
            worker = ExtractThreadWorker(self._organizer, mod_list, arc_file)
            worker.signals.result.connect(self.extract_thread_worker_output)
            worker.signals.finished.connect(self.extract_thread_worker_complete)
            # Execute
            self.threadpool.start(worker)

    def extract_thread_cleanup(self):  # called after completion of all ExtractThreadWorker()
        organizer = self._organizer
        mod_directory = organizer.modsPath()
        # get mod active list
        mod_active_list = []
        modlist = organizer.modList()
        if bool(organizer.pluginSetting(self.name(), "log-enabled")):
            self.logger.debug("Starting cleanup")
        for mod_name in modlist.allModsByProfilePriority():
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if "Merged ARC" not in mod_name:
                    mod_active_list.append(mod_name)
        for mod_name in mod_active_list:
            for dirpath, dirnames, filenames in os.walk(
                f"{mod_directory}/{mod_name}", topdown=False
            ):
                for dirname in dirnames:
                    full_path = os.path.join(dirpath, dirname)
                    if not os.listdir(full_path):
                        if bool(organizer.pluginSetting(self.name(), "verbose-log")):
                            self.logger.debug("Deleting %s", full_path)
                        os.rmdir(full_path)
                        pathlib.Path(f"{full_path}.arc.txt").unlink(missing_ok=True)
        # announce completion
        self.extract_progress_dialog.hide()
        QMessageBox.information(
            self.__parent_widget, self.__tr("Extraction complete"), self.__tr(
                "Duplicate ARC count: %s\n" % len(self.arc_files_duplicate_dict)
                + "Unique ARC count: %s" % len(self.arc_files_seen_dict))
        )
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            self.logger.debug("Extraction complete")
            # clear handlers. We're done
            self.logger.handlers.clear()

    def extract_thread_worker_complete(
        self,
    ):  # called after completion of each extractThreadWorker()
        self.current_index += 1
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            self.logger.debug(
                "Extract index: %s : %s",
                self.current_index,
                self.extract_progress_dialog.maximum(),
            )
        if self.current_index == self.extract_progress_dialog.maximum():
            self.extract_thread_cleanup()
        if self.extract_progress_dialog.wasCanceled():
            ARCExtract.threadCancel = True
        else:
            self.extract_progress_dialog.setValue(self.current_index)

    def extract_thread_worker_output(self, log_out):
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            self.logger.debug(log_out)


class ScanThreadWorkerSignals(QObject):
    finished = pyqtSignal()
    progress = pyqtSignal(int)
    result = pyqtSignal(str)


class ScanThreadWorker(QRunnable):
    def __init__(self, organizer, mod_active_list):
        self._organizer = organizer
        self._mod_active_list = mod_active_list
        self.signals = ScanThreadWorkerSignals()
        super(ScanThreadWorker, self).__init__()

    @pyqtSlot()
    def run(self):
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        log_out = "\n"
        mod_directory = self._organizer.modsPath()
        merge_mod = "Merged ARC - " + self._organizer.profileName()
        previous_merge_file = os.path.join(
            mod_directory, merge_mod, "arcFileMerge.json"
        )

        # create merge folder if not exist
        pathlib.Path(
            os.path.join(
                mod_directory,
                merge_mod,
            )
        ).mkdir(parents=True, exist_ok=True)

        # load previous arc merge info
        if os.path.isfile(previous_merge_file):
            try:
                with open(
                    previous_merge_file,
                    "r",
                    encoding="utf-8",
                ) as file_handle:
                    ARCExtract.arc_folders_previous_build_dict = json.load(file_handle)
            except IOError:
                if bool(self._organizer.pluginSetting(self._name(), "log-enabled")):
                    log_out += "arcFileMerge.json not found or invalid"

        mods_scanned = 0
        # build list of active mod duplicate arc files to extract
        for mod_name in self._mod_active_list:
            if ARCExtract.threadCancel:
                return
            log_out += f"Scanning: {mod_name}\n"
            # if merge mode, compare game directory files and remove duplicates here
            if bool(self._organizer.pluginSetting("ARC Extract", "merge-mode")):
                log_out += "Merge mod creation enabled\n"

                def list_identical_files(dcmp):
                    filelist = []
                    for name in dcmp.same_files:
                        filelist.append(os.path.join(dcmp.right, name))
                    for sub_dcmp in dcmp.subdirs.values():
                        for name in list_identical_files(sub_dcmp):
                            filelist.append(name)
                    return filelist

                dcmp = filecmp.dircmp(game_directory, os.path.join(mod_directory, mod_name),)
                files_to_delete = list_identical_files(dcmp)
                if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                    log_out += "------ deleting files matching game folder ------\n"
                    for name in files_to_delete:
                        log_out += f'Removing "{name}"\n'
                    log_out += "------ end output ------\n"
                if bool(
                    self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "log-enabled")):
                    log_out += f"Removed {len(files_to_delete)} identical to game folder files\n"
                for name in files_to_delete:
                    os.remove(name)
            for dirpath, dirnames, filenames in os.walk(os.path.join(mod_directory, mod_name)):
                # check for extracted arc folders
                for folder in dirnames:
                    full_path = os.path.join(dirpath, folder + ".arc")
                    relative_path = os.path.relpath(full_path, mod_directory).split(os.path.sep, 1)[1]
                    if os.path.isfile(os.path.normpath(game_directory + os.path.sep + relative_path)):
                        if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                            log_out += f"ARC Folder: {full_path}\n"
                        if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "merge-mode")):
                            ARCExtract.arc_files_seen_dict[relative_path].append(mod_name)
                        if (relative_path in ARCExtract.arc_files_seen_dict):
                            mod_where_first_seen = ARCExtract.arc_files_seen_dict[relative_path][0]
                            ARCExtract.arc_files_duplicate_dict[relative_path].append(mod_where_first_seen)
                            if (mod_name not in ARCExtract.arc_files_duplicate_dict[relative_path]):
                                ARCExtract.arc_files_duplicate_dict[relative_path].append(mod_name)
                        else:
                            if (mod_name not in ARCExtract.arc_files_seen_dict[relative_path]):
                                ARCExtract.arc_files_seen_dict[relative_path].append(mod_name)
                # check for arc files
                for file in filenames:
                    if file.endswith(".arc"):
                        full_path = os.path.join(dirpath, file)
                        relative_path = os.path.relpath(full_path, mod_directory).split(os.path.sep, 1)[1]
                        if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "merge-mode")):
                            if (mod_name not in ARCExtract.arc_files_seen_dict[relative_path]):
                                ARCExtract.arc_files_seen_dict[relative_path].append(mod_name)
                        if (relative_path in ARCExtract.arc_files_seen_dict):
                            mod_where_first_seen = ARCExtract.arc_files_seen_dict[relative_path][0]
                            ARCExtract.arc_files_duplicate_dict[relative_path].append(mod_where_first_seen)
                            log_out += f"Duplicate ARC: {os.path.join(dirpath, file)}\n"
                            if (mod_name not in ARCExtract.arc_files_duplicate_dict[relative_path]):
                                ARCExtract.arc_files_duplicate_dict[relative_path].append(mod_name)
                            # update arc_folders_previous_build_dict
                            # strip .arc extension
                            relative_folder_path = os.path.splitext(relative_path)[0]
                            if (relative_folder_path in ARCExtract.arc_folders_previous_build_dict and mod_name in ARCExtract.arc_folders_previous_build_dict[relative_folder_path]):
                                ARCExtract.arc_folders_previous_build_dict[relative_folder_path].remove(mod_name)
                                # update arcFileMerge.json
                                try:
                                    with open(os.path.join(mod_directory, merge_mod, "arcFileMerge.json",), "w", encoding="utf-8",) as file_handle:
                                        json.dump(ARCExtract.arc_folders_previous_build_dict, file_handle,)
                                except IOError:
                                    if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "log-enabled")):
                                        log_out += ("arcFileMerge.json missing or invalid")
                        else:
                            if (mod_name not in ARCExtract.arc_files_seen_dict[relative_path]):
                                ARCExtract.arc_files_seen_dict[relative_path].append(mod_name)
            mods_scanned += 1
            self.signals.progress.emit(mods_scanned)  # update progress
        self.signals.result.emit(log_out)  # Return log
        self.signals.finished.emit()  # Done
        return


class ExtractThreadWorkerSignals(QObject):
    finished = pyqtSignal()
    result = pyqtSignal(str)


class ExtractThreadWorker(QRunnable):
    def __init__(self, organizer, mod_list, arc_file):
        self._organizer = organizer
        self._mod_list = mod_list
        self._arc_file = arc_file
        self.signals = ExtractThreadWorkerSignals()
        super(ExtractThreadWorker, self).__init__()

    @pyqtSlot()
    def run(self):
        # check for cancellation
        if ARCExtract.threadCancel:
            return
        args = "-x -pc -dd -alwayscomp -txt -v 7"
        executable = os.path.join(self._organizer.basePath(), "ARCtool.exe")
        arc_file_parent_relpath = os.path.dirname(self._arc_file)
        extracted_arc_folder_relpath = os.path.splitext(self._arc_file)[0]
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        mod_directory = self._organizer.modsPath()
        merge_mod = "Merged ARC - " + self._organizer.profileName()
        arc_file_fullpath = os.path.join(mod_directory, merge_mod, self._arc_file)
        log_out = "\n"
        # extract vanilla if needed
        extracted_arc_folder_fullpath = os.path.join(
            mod_directory, merge_mod, extracted_arc_folder_relpath
        )
        for mod_name in self._mod_list:
            arc_fullpath = os.path.join(mod_directory, mod_name, self._arc_file)
            if os.path.isfile(arc_fullpath):
                log_out += f"Extracting: {mod_name} {self._arc_file}\n"
                # extract arc
                command = f'"{executable}" {args} "{arc_fullpath}"'
                if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                    log_out += "Extract command: " + command + "\n"
                command_out = os.popen(command).read()
                if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                    log_out += "------ start arctool output ------\n"
                    log_out += command_out + "------ end arctool output ------\n"
                if not os.path.isdir(extracted_arc_folder_fullpath):
                    log_out += f"Extracting vanilla ARC: {self._arc_file}\n"
                if os.path.isfile(os.path.join(game_directory, self._arc_file)):
                    pathlib.Path(extracted_arc_folder_fullpath).mkdir(parents=True, exist_ok=True)
                    shutil.copy(os.path.join(game_directory, self._arc_file),os.path.join(mod_directory, merge_mod, arc_file_parent_relpath),)
                    command = f'"{executable}" {args} "{arc_file_fullpath}"'
                    command_out = os.popen(command).read()
                    if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                        log_out += "------ start arctool output ------\n"
                        log_out += command_out + "------ end arctool output ------\n"
                    # remove .arc file
                    os.remove(arc_file_fullpath)
                # remove ITM
                if bool(self._organizer.pluginSetting("ARC Extract", "remove-ITM")):
                    log_out += "Removing ITM\n"

                    def list_identical_files(dcmp):
                        filelist = []
                        try:
                            for name in dcmp.same_files:
                                filelist.append(os.path.join(dcmp.right, name))
                        except OSError as e:
                            # do nothing
                            error = e
                        for sub_dcmp in dcmp.subdirs.values():
                            for name in list_identical_files(sub_dcmp):
                                filelist.append(name)
                        return filelist

                    # compare mod folder to extracted vanilla arc folder
                    dcmp = filecmp.dircmp(os.path.join(mod_directory, merge_mod, extracted_arc_folder_relpath,), os.path.join(mod_directory, mod_name, extracted_arc_folder_relpath),)
                    files_to_delete = list_identical_files(dcmp)
                    if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                        log_out += "------ deleting files matching vanilla extracted arc folder ------\n"
                        for name in files_to_delete:
                            log_out += f'Removing "{name}"\n'
                        log_out += "------ end output ------\n"
                    if bool(
                        self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "log-enabled")):
                        log_out += f"Removed {len(files_to_delete)} identical files\n"
                    for name in files_to_delete:
                        os.remove(name)

                    # delete empty folders
                    for dirpath, dirnames, filenames in os.walk(
                        os.path.join(mod_directory, mod_name, extracted_arc_folder_relpath), topdown=False,):
                        for dirname in dirnames:
                            full_path = os.path.join(dirpath, dirname)
                            if not os.listdir(full_path):
                                if bool(self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "verbose-log")):
                                    log_out += f"Removed empty folder: {full_path}\n"
                                os.rmdir(full_path)
                                pathlib.Path(f"{full_path}.arc.txt").unlink(missing_ok=True)
                # delete arc
                if bool(
                    self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "delete-ARC")):
                    log_out += f"Deleting {arc_fullpath}\n"
                    pathlib.Path(arc_fullpath).unlink(missing_ok=True)
                # remove .arc.txt
                if not bool(
                    self._organizer.pluginSetting(ARCExtract.name(ARCExtract), "merge-mode")):
                    pathlib.Path(f"{arc_fullpath}.txt").unlink(missing_ok=True)
                log_out += "ARC extract complete"
        if log_out != "\n":
            self.signals.result.emit(log_out)  # Return log
        self.signals.finished.emit()  # Done
        return


def createPlugin():
    return ARCExtract()
