# This Mod Organizer plugin is released to the pubic under the terms of the
# GNU GPL version 3, which is accessible from the Free Software Foundation
# here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

""" add support for ARCtool """

import os
import json
import shutil
import hashlib
import logging
import pathlib
from collections import defaultdict

from PyQt6.QtCore import (
    QFileInfo,
    QThreadPool,
    QRunnable,
    QObject,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QIcon, QFileSystemModel
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog

import mobase


class ARCtoolInvalidPathException(Exception):
    """Thrown if ARCtool.exe path can't be found"""


class ARCtoolMissingException(Exception):
    """Thrown if selected ARC file can't be found"""

class ARCMerge(mobase.IPluginTool):
    arc_folders_previous_build_dict = defaultdict(list)
    arc_folders_current_build_dict = defaultdict(list)
    threadCancel = False

    def __init__(self):
        super(ARCMerge, self).__init__()
        self._organizer = None
        self.threadpool = None
        self.current_index = 0
        self.merge_progress_dialog = None
        self.logger = None
        self.__parent_widget = None

    def init(self, organizer):
        self._organizer = organizer
        self.threadpool = QThreadPool()
        return True

    def name(self):
        return "ARC Merge"

    def localizedName(self):
        return self.__tr("ARC Merge")

    def author(self):
        return "MikoMuto"

    def description(self):
        return self.__tr("Runs ARCtool on mods to merge extracted .arc folders from mods")

    def version(self):
        return mobase.VersionInfo(2, 0, 1, 0)

    def requirements(self):
        return [mobase.PluginRequirementFactory.gameDependency("Dragon's Dogma: Dark Arisen")]

    def isActive(self) -> bool:
        return self._organizer.pluginSetting(self.main_tool_name(), "enabled")

    def settings(self):
        return []

    def displayName(self):
        return self.__tr("ARC Merge")

    def tooltip(self):
        return self.__tr("Merge extracted .arc files")

    def icon(self):
        return QIcon(":/MO/gui/content/plugin")

    def setParentWidget(self, widget):
        self.__parent_widget = widget

    def display(self):
        initialized = self._organizer.pluginSetting(
            self.main_tool_name(), "initialised"
        )
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
        if self._organizer.pluginSetting(self.main_tool_name(), "log-enabled"):
            log_file = self._organizer.overwritePath() + "\\ARCMerge.log"
            self.logger = logging.getLogger("am_logger")
            f_handler = logging.FileHandler(log_file, "w+")
            f_handler.setLevel(logging.DEBUG)
            f_format = logging.Formatter("%(asctime)s %(message)s")
            f_handler.setFormatter(f_format)
            self.logger.addHandler(f_handler)
            self.logger.propagate = False

        # check for inactive mods
        if self._organizer.pluginSetting(self.main_tool_name(), "uncheck-mods"):
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
        self.__process_mods()

    def get_arctool(self):
        arctool_path = os.path.join(self._organizer.basePath(), "ARCtool.exe")
        if not os.path.isfile(arctool_path):
            raise ARCtoolMissingException

    def __tr(self, txt: str) -> str:
        return QApplication.translate("ARCMerge", txt)

    def show_activate_dialog(self, mod_name):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(f"Mod {mod_name} is disabled. Do you wish to enable it?")
        msg.setInformativeText("Disabled mods will not be included in the merge process.")
        msg.setStandardButtons(
            QMessageBox.StandardButton.YesToAll
            | QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.NoToAll
        )
        retval = msg.exec()
        return retval

    def __process_mods(self):  # called from display()
        self.arc_folders_previous_build_dict.clear()
        self.arc_folders_current_build_dict.clear()

        # reset cancelled flag
        ARCMerge.threadCancel = False
        # get mod active list
        active_mod_list = []
        modlist = self._organizer.modList()
        for mod_name in modlist.allModsByProfilePriority():
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if "Merged ARC" not in mod_name:
                    active_mod_list.append(mod_name)
        # set mod count for progress
        self.merge_progress_dialog = QProgressDialog(self.__tr("Scanning..."), self.__tr("Cancel"), 0, 0, self.__parent_widget)
        self.merge_progress_dialog.setFixedWidth(300)
        self.merge_progress_dialog.setMaximum(len(active_mod_list))
        self.merge_progress_dialog.forceShow()
        # set max thread count
        self.threadpool.setMaxThreadCount(self._organizer.pluginSetting(self.main_tool_name(), "max-threads"))
        # start single scan thread
        worker = ScanThreadWorker(self._organizer, active_mod_list)
        worker.signals.progress.connect(self.scan_thread_worker_progress)
        worker.signals.result.connect(self.scan_thread_worker_output)
        worker.signals.finished.connect(self.scan_thread_worker_complete)
        # Execute
        self.threadpool.start(worker)

    def merge_arc_files(self):
        merge_needed_count = 0
        # process changed merges from dictionary
        for entry in self.arc_folders_current_build_dict:
            if (entry not in self.arc_folders_previous_build_dict or self.arc_folders_current_build_dict[entry] != self.arc_folders_previous_build_dict[entry]):
                # Pass the function to execute
                worker = MergeThreadWorker(self._organizer, self.arc_folders_current_build_dict[entry], entry)
                worker.signals.result.connect(self.merge_thread_worker_output)
                worker.signals.finished.connect(self.merge_thread_worker_complete)
                # Execute
                self.threadpool.start(worker)
                merge_needed_count += 1
        if bool(self._organizer.pluginSetting(self.main_tool_name(), "log-enabled")):
            self.logger.debug("ARC merge count: %s", merge_needed_count)
        # progress reinit
        self.merge_progress_dialog.setLabelText("Merging...")
        self.merge_progress_dialog.setValue(0)
        self.merge_progress_dialog.setMaximum(merge_needed_count)
        self.merge_progress_dialog.forceShow()

        if merge_needed_count == 0:
            self.mod_cleanup()

    def mod_cleanup(self):
        mod_directory = self.__get_mod_directory()
        merge_mod = "Merged ARC - " + self._organizer.profileName()
        self.merge_progress_dialog.setLabelText(self.__tr("Cleaning up..."))

        if bool(self._organizer.pluginSetting(self.main_tool_name(), "log-enabled")):
            self.logger.debug("Cleaning up...")
        # remove stale .arc files from merged folder
        for entry in self.arc_folders_previous_build_dict:
            if self.merge_progress_dialog.wasCanceled():
                if bool(self._organizer.pluginSetting(self.main_tool_name(), "log-enabled")):
                    self.logger.debug("Merge cancelled")
                return
            if entry not in self.arc_folders_current_build_dict:
                if bool(self._organizer.pluginSetting(self.main_tool_name(), "log-enabled")):
                    self.logger.debug("Deleting stale arc: %s", entry)
                # Pass the function to execute
                worker = CleanupThreadWorker(self._organizer, entry)
                # Execute
                self.threadpool.start(worker)
        # write arc merge info to json
        try:
            with open(os.path.join(mod_directory, merge_mod, "arcFileMerge.json"), "w", encoding="utf-8",) as file_handle:
                json.dump(self.arc_folders_current_build_dict, file_handle)
        except IOError:
            if bool(self._organizer.pluginSetting(self.main_tool_name(), "log-enabled")):
                self.logger.debug("arcFileMerge.json not found or invalid")

        if self._organizer.pluginSetting(self.main_tool_name(), "uncheck-mods"):
            # disable all invalid mods
            modlist = self._organizer.modList()
            for mod_name in modlist.allModsByProfilePriority():
                if not modlist.state(mod_name) & mobase.ModState.VALID:
                    self._organizer.modList().setActive(mod_name, False)
        self.merge_progress_dialog.hide()
        QMessageBox.information(
            self.__parent_widget, self.__tr(""), self.__tr("Merge complete")
        )
        self.logger.handlers.clear()
        # self._organizer.refresh()
        # enable merge mod
        self._organizer.modList().setActive(merge_mod, True)

    def scan_thread_worker_progress(
        self, progress
    ):  # called after each mod is scanned in ScanThreadWorker()
        if self.merge_progress_dialog.wasCanceled():
            ARCMerge.threadCancel = True
        else:
            self.merge_progress_dialog.setValue(progress)

    def scan_thread_worker_complete(
        self,
    ):  # called after completion of ScanThreadWorker()
        if bool(self._organizer.pluginSetting(self.main_tool_name(), "log-enabled")):
            self.logger.debug("Scan complete")
            self.logger.debug("Previous count: %d", len(self.arc_folders_previous_build_dict))
            self.logger.debug("Current count: %s", len(self.arc_folders_current_build_dict))
        # start merge
        self.merge_arc_files()

    def scan_thread_worker_output(self, log_out):
        if bool(self._organizer.pluginSetting(self.main_tool_name(), "log-enabled")):
            self.logger.debug(log_out)

    def merge_thread_worker_complete(self):
        self.current_index += 1
        if bool(self._organizer.pluginSetting(self.main_tool_name(), "log-enabled")):
            self.logger.debug(
                "Merge index: %s : %s",
                self.current_index,
                self.merge_progress_dialog.maximum(),
            )
        if self.current_index == self.merge_progress_dialog.maximum():
            self.current_index = 0
            self.mod_cleanup()
        if self.merge_progress_dialog.wasCanceled():
            self.current_index = 0
            ARCMerge.threadCancel = True
        else:
            self.merge_progress_dialog.setValue(self.current_index)

    def merge_thread_worker_output(self, log_out):
        if bool(self._organizer.pluginSetting(self.main_tool_name(), "log-enabled")):
            self.logger.debug(log_out)

    def __get_mod_directory(self):
        return self._organizer.modsPath()

    @staticmethod
    def main_tool_name():
        return "ARC Extract"


class ScanThreadWorkerSignals(QObject):
    finished = pyqtSignal()
    progress = pyqtSignal(int)
    result = pyqtSignal(str)


class ScanThreadWorker(QRunnable):
    def __init__(self, organizer, active_mod_list):
        self._organizer = organizer
        self.active_mod_list = active_mod_list
        self.signals = ScanThreadWorkerSignals()
        super(ScanThreadWorker, self).__init__()

    @pyqtSlot()
    def run(self):
        log_out = "\n"
        mod_directory = self._organizer.modsPath()
        modlist = self._organizer.modList()
        merge_mod = "Merged ARC - " + self._organizer.profileName()
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        previous_merge_file = os.path.join(
            mod_directory, merge_mod, "arcFileMerge.json"
        )

        # create merge folder if not exist
        pathlib.Path(os.path.join(mod_directory, merge_mod, )).mkdir(parents=True, exist_ok=True)

        # load previous arc merge info
        if os.path.isfile(previous_merge_file):
            try:
                with open(previous_merge_file, "r", encoding="utf-8", ) as file_handle:
                    ARCMerge.arc_folders_previous_build_dict = json.load(file_handle)
            except IOError:
                if bool(self._organizer.pluginSetting(ARCMerge.main_tool_name(), "log-enabled")):
                    log_out += "arcFileMerge.json not found or invalid"

        mods_scanned = 0
        # build list of current active mod arc folders to merge
        for mod_name in self.active_mod_list:
            # check for cancellation
            if ARCMerge.threadCancel:
                return
            mods_scanned += 1
            self.signals.progress.emit(mods_scanned)  # update progress
            log_out += f"Scanning: {mod_name}\n"
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if "Merged ARC" not in mod_name:
                    for dirpath, dirnames, filenames in os.walk(mod_directory + os.path.sep + mod_name):
                        # check for extracted arc folders
                        for folder in dirnames:
                            arc_folder = dirpath + os.path.sep + folder
                            relative_path = os.path.relpath(arc_folder, mod_directory).split(os.path.sep, 1)[1]
                            # check for matching game file or arc.txt
                            #  (fix for gog to steam merge)
                            if os.path.isfile(os.path.join(game_directory, relative_path + ".arc")) or os.path.isfile(os.path.join(mod_directory, mod_name, relative_path + ".arc.txt", )):
                                if bool(self._organizer.pluginSetting(ARCMerge.main_tool_name(), "verbose-log")):
                                    log_out += f"ARC Folder: {relative_path}\n"
                                if (mod_name not in ARCMerge.arc_folders_current_build_dict[relative_path]):
                                    ARCMerge.arc_folders_current_build_dict[relative_path].append(mod_name)

        self.signals.result.emit(log_out)  # Return log
        self.signals.finished.emit()  # Done
        return


class CleanupThreadWorker(QRunnable):
    def __init__(self, organizer, entry):
        self._organizer = organizer
        self.entry = entry
        super(CleanupThreadWorker, self).__init__()

    @pyqtSlot()
    def run(self):
        mod_directory = self._organizer.modsPath()
        merge_mod = "Merged ARC - " + self._organizer.profileName()
        # clean merge
        pathlib.Path(os.path.join(mod_directory, merge_mod, self.entry + ".arc.txt")).unlink(missing_ok=True)
        pathlib.Path(os.path.join(mod_directory, merge_mod, self.entry + ".arc")).unlink(missing_ok=True)
        if os.path.exists(os.path.join(mod_directory, merge_mod, self.entry)):
            shutil.rmtree(os.path.join(mod_directory, merge_mod, self.entry))
        return


class MergeThreadWorkerSignals(QObject):
    finished = pyqtSignal()
    result = pyqtSignal(str)


class MergeThreadWorker(QRunnable):
    def __init__(self, organizer, mods_to_merge, arc_folder_path):
        self._organizer = organizer
        self.mods_to_merge = mods_to_merge
        self.arc_folder_path = arc_folder_path
        self.signals = MergeThreadWorkerSignals()
        super(MergeThreadWorker, self).__init__()

    @pyqtSlot()
    def run(self):
        log_out = ""
        if ARCMerge.threadCancel:
            log_out += "Merge cancelled\n"
            return
        extract_args = "-x -pc -dd -alwayscomp -txt -v 7"
        compress_args = "-c -pc -dd -alwayscomp -tex -xfs -gmd -txt -v 7"
        executable = os.path.join(self._organizer.basePath(), "ARCtool.exe")
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        mod_directory = self._organizer.modsPath()
        arc_folder_parent = os.path.dirname(self.arc_folder_path)
        merge_mod = "Merged ARC - " + self._organizer.profileName()
        # copy vanilla arc to merge folder, extract, then delete if not already done
        extracted_arc_folder = os.path.join(mod_directory, merge_mod, self.arc_folder_path)
        if not os.path.isdir(extracted_arc_folder):
            log_out += f'Extracting vanilla ARC: {self.arc_folder_path + ".arc"}\n'
            if os.path.isfile(os.path.join(game_directory, self.arc_folder_path + ".arc")):
                pathlib.Path(os.path.join(mod_directory, merge_mod, arc_folder_parent, "")).mkdir(parents=True, exist_ok=True)
                shutil.copy(os.path.join(game_directory, self.arc_folder_path + ".arc"), os.path.join(mod_directory, merge_mod, arc_folder_parent, ""), )
                arc_fullpath = extracted_arc_folder + ".arc"
                command = f'"{executable}" {extract_args} "{arc_fullpath}"'
                output = os.popen(command).read()
                if bool(self._organizer.pluginSetting(ARCMerge.main_tool_name(), "verbose-log")):
                    log_out += "\n------ start arctool vanilla extract output ------\n"
                    log_out += output + "------ end output ------\n"
                # remove .arc file
                os.remove(os.path.join(mod_directory, merge_mod, self.arc_folder_path + ".arc"))
        # copy mod files to merge folder
        for mod_name in self.mods_to_merge:
            child_mod_arc_path = os.path.join(mod_directory, mod_name, self.arc_folder_path)
            if pathlib.Path(child_mod_arc_path).exists():
                log_out += f"Merging mod: {mod_name}\n"
                shutil.copytree(child_mod_arc_path, os.path.join(mod_directory, merge_mod, self.arc_folder_path, ""), dirs_exist_ok=True, )
            if os.path.isfile(child_mod_arc_path + ".arc.txt"):
                log_out += f"Copying {mod_name} {self.arc_folder_path}.arc.txt\n"
                shutil.copy(child_mod_arc_path + ".arc.txt", os.path.join(mod_directory, merge_mod, arc_folder_parent, ), )
        # compress
        arc_fullpath = os.path.join(mod_directory, merge_mod, self.arc_folder_path)
        command = f'"{executable}" {compress_args} "{arc_fullpath}"'
        output = os.popen(command).read()
        if bool(self._organizer.pluginSetting(ARCMerge.main_tool_name(), "verbose-log")):
            log_out += "------ start arctool merge output ------\n"
            log_out += output + "------ end output ------\n"
        # remove folders and txt
        log_out += "Removing temp files\n"
        shutil.rmtree(arc_fullpath)
        pathlib.Path(arc_fullpath + ".arc.txt").unlink(missing_ok=True)
        # finished
        log_out += "ARC merge complete"
        self.signals.result.emit(log_out)  # Return logs
        self.signals.finished.emit()  # Done
        return


def createPlugin():
    return ARCMerge()
