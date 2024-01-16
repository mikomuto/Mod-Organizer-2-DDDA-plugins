# This Mod Organizer plugin is released to the pubic under the terms of the
# GNU GPL version 3, which is accessible from the Free Software Foundation
# here: https://www.gnu.org/licenses/gpl-3.0-standalone.html

""" add support for ARCtool """


import os
import filecmp
import logging
import pathlib
import shutil
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


class ARCToolInvalidPathException(Exception):
    """Thrown if ARCTool.exe path can't be found"""


class ARCToolMissingException(Exception):
    """Thrown if selected ARC file can't be found"""


class ARCFileMissingException(Exception):
    """Thrown if selected ARC file can't be found"""


class ARCExtract(mobase.IPluginTool):
    arc_files_seen_dict = defaultdict(list)
    arc_files_duplicate_dict = defaultdict(list)
    threadCancel = False

    def __init__(self):
        super(ARCExtract, self).__init__()
        self._organizer = None
        self.threadpool = None
        self.current_index = 0
        self.extract_progress_dialog = None
        self.logger = None
        self.__parent_widget = None

    def init(self, organizer):
        self._organizer = organizer
        self.threadpool = QThreadPool()
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
            mobase.PluginRequirementFactory.gameDependency(
                "Dragon's Dogma: Dark Arisen"
            )
        ]

    def isActive(self) -> bool:
        return self._organizer.pluginSetting(self.name(), "enabled")

    def settings(self):
        return [
            mobase.PluginSetting("enabled", "enable this plugin", True),
            mobase.PluginSetting("ARCTool-path", self.__tr("Path to ARCTool.exe"), ""),
            mobase.PluginSetting(
                "initialised",
                self.__tr(
                    "Settings have been initialised.  Set to False to reinitialise them."
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
                    "Enable logging. Log file can be found in the ARCTool mod folder"
                ),
                False,
            ),
            mobase.PluginSetting(
                "verbose-log", self.__tr("Verbose logs. More info!!!"), False
            ),
            mobase.PluginSetting(
                "uncheck-mods",
                self.__tr(
                    "Uncheck ARCTool mod and mods without valid game data after merge."
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
        return self.__tr("Unpacks all ARC files")

    def icon(self):
        arc_tool_path = self._organizer.pluginSetting(self.name(), "ARCTool-path")
        if os.path.exists(arc_tool_path):
            # We can't directly grab the icon from an executable,
            # but this seems like the simplest alternative.
            fin = QFileInfo(arc_tool_path)
            model = QFileSystemModel()
            model.setRootPath(fin.path())
            return model.fileIcon(model.index(fin.filePath()))
        else:
            # Fall back to where the user might have put an icon manually.
            return QIcon("plugins/ARCTool.ico")

    def setParentWidget(self, widget):
        self.__parent_widget = widget

    def display(self):
        if not bool(self._organizer.pluginSetting(self.name(), "initialised")):
            # reset all
            self._organizer.setPluginSetting(self.name(), "ARCTool-path", "")
            self._organizer.setPluginSetting(self.name(), "remove-ITM", True)
            self._organizer.setPluginSetting(self.name(), "delete-ARC", True)
            self._organizer.setPluginSetting(self.name(), "log-enabled", False)
            self._organizer.setPluginSetting(self.name(), "verbose-log", False)
            self._organizer.setPluginSetting(self.name(), "uncheck-mods", True)
            self._organizer.setPluginSetting(
                self.name(), "max-threads", self.threadpool.maxThreadCount()
            )
            self._organizer.setPluginSetting(self.name(), "merge-mode", False)
        try:
            executable = self.get_arctool_path()
        except ARCToolInvalidPathException:
            QMessageBox.critical(
                self.__parent_widget,
                self.__tr("ARCTool path not specified"),
                self.__tr(
                    "The path to ARCTool.exe wasn't specified. The tool will now exit."
                ),
            )
            return
        except ARCToolMissingException:
            QMessageBox.critical(
                self.__parent_widget,
                self.__tr("ARCTool not found"),
                self.__tr("ARCTool.exe not found. Resetting tool."),
            )
            return

        # reset cancelled flag
        ARCExtract.threadCancel = False
        self._organizer.setPluginSetting(self.name(), "initialised", True)

        # logger setup
        arctool_path = self._organizer.pluginSetting(self.name(), "ARCTool-path")
        log_file = os.path.dirname(arctool_path) + "\\ARCExtract.log"
        self.logger = logging.getLogger("ae_logger")
        f_handler = logging.FileHandler(log_file, "w+")
        f_handler.setLevel(logging.DEBUG)
        f_format = logging.Formatter("%(asctime)s %(message)s")
        f_handler.setFormatter(f_format)
        self.logger.addHandler(f_handler)
        self.logger.propagate = False

        # run the stuff
        self.process_mods(executable)

    def __tr(self, txt: str) -> str:
        return QApplication.translate("ARCTool", txt)

    def get_arctool_path(self):
        saved_path = self._organizer.pluginSetting(self.name(), "ARCTool-path")
        # ARCTool must be installed within the game's data directory or a mod folder
        mod_directory = self._organizer.modsPath()
        game_directory = pathlib.Path(
            self._organizer.managedGame().dataDirectory().absolutePath()
        )
        arctool_file_path = pathlib.Path(saved_path)
        if not os.path.exists(arctool_file_path):
            self._organizer.setPluginSetting(self.name(), "ARCTool-path", "")
            self._organizer.setPluginSetting(self.name(), "initialised", False)
            raise ARCToolMissingException
        in_good_location = self.__within_directory(arctool_file_path, mod_directory)
        in_good_location |= self.__within_directory(arctool_file_path, game_directory)
        if not arctool_file_path.is_file() or not in_good_location:
            QMessageBox.warning(
                self.__parent_widget,
                self.__tr(""),
                self.__tr(
                    "ARCTool path invalid or not set. \n\nARCTool must be visible"
                    + "within the VFS, choose an installation within a mod folder. \n\n"
                    + "This setting can be updated in the Plugins tab of the Mod"
                    + "Organizer Settings menu."
                ),
            )
            while True:
                path = QFileDialog.getOpenFileName(
                    self.__parent_widget,
                    self.__tr("Locate ARCTool.exe"),
                    str(mod_directory),
                    "ARCTool.exe",
                )[0]
                if path == "":
                    # Cancel was pressed
                    raise ARCToolInvalidPathException
                arctool_file_path = pathlib.Path(path)
                in_good_location = self.__within_directory(
                    arctool_file_path, mod_directory
                )
                in_good_location |= self.__within_directory(
                    arctool_file_path, game_directory
                )
                if arctool_file_path.is_file() and in_good_location:
                    self._organizer.setPluginSetting(self.name(), "ARCTool-path", path)
                    saved_path = path
                    break
                else:
                    QMessageBox.information(
                        self.__parent_widget,
                        self.__tr("Not a compatible location..."),
                        self.__tr(
                            """ARCTool only works when within the VFS, so must be
                             installed within a mod folder. Please select a different
                             ARC installation"""
                        ),
                    )
        return saved_path

    def process_mods(self, executable):  # called from display()
        self.arc_files_seen_dict.clear()
        self.arc_files_duplicate_dict.clear()
        mod_directory = self._organizer.modsPath()
        executable_path, executable_name = os.path.split(executable)
        arctool_mod = os.path.relpath(executable_path, mod_directory).split(
            os.path.sep, 1
        )[0]

        # get mod active list
        mod_active_list = []
        modlist = self._organizer.modList()
        for mod_name in modlist.allModsByProfilePriority():
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if mod_name != arctool_mod and "Merged ARC" not in mod_name:
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
        self.extract_duplicate_arcs()

    def scan_thread_worker_output(self, log_out):
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            self.logger.debug(log_out)

    def extract_duplicate_arcs(
        self,
    ):  # called after completion of buildDuplicatesDictionary()
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

    def extract_thread_cleanup(
        self,
    ):  # called after completion of all ExtractThreadWorker()
        organizer = self._organizer
        executable = self.get_arctool_path()
        mod_directory = self._organizer.modsPath()
        executable_path, executable_name = os.path.split(executable)
        arctool_mod = os.path.relpath(executable_path, mod_directory).split(
            os.path.sep, 1
        )[0]
        # get mod active list
        mod_active_list = []
        modlist = organizer.modList()
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            self.logger.debug("Starting cleanup")
        for mod_name in modlist.allModsByProfilePriority():
            if modlist.state(mod_name) & mobase.ModState.ACTIVE:
                if mod_name != arctool_mod and "Merged ARC" not in mod_name:
                    mod_active_list.append(mod_name)
        for mod_name in mod_active_list:
            for dirpath, dirnames, filenames in os.walk(
                f"{mod_directory}/{mod_name}", topdown=False
            ):
                for dirname in dirnames:
                    full_path = os.path.join(dirpath, dirname)
                    if not os.listdir(full_path):
                        if bool(
                            self._organizer.pluginSetting(self.name(), "verbose-log")
                        ):
                            self.logger.debug("Deleting %s", full_path)
                        os.rmdir(full_path)
                        pathlib.Path(f"{full_path}.arc.txt").unlink(missing_ok=True)
        self.announce_finish()

    def announce_finish(self):
        self.extract_progress_dialog.hide()
        QMessageBox.information(
            self.__parent_widget, self.__tr(""), self.__tr("Extraction complete")
        )
        if bool(self._organizer.pluginSetting(self.name(), "log-enabled")):
            self.logger.debug("Extraction complete")

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

    @staticmethod
    def __within_directory(inner_path, outer_dir):
        for path in inner_path.parents:
            if path.samefile(outer_dir):
                return True
        return False


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
        mod_directory = self._organizer.modsPath()
        log_out = "\n"
        mods_scanned = 0
        # build list of active mod duplicate arc files to extract
        for mod_name in self._mod_active_list:
            if ARCExtract.threadCancel:
                return
            log_out += f"Scanning: {mod_name}\n"
            for dirpath, dirnames, filenames in os.walk(
                os.path.join(mod_directory, mod_name)
            ):
                # check for extracted arc folders
                for folder in dirnames:
                    full_path = os.path.join(dirpath, folder + ".arc")
                    relative_path = os.path.relpath(full_path, mod_directory).split(
                        os.path.sep, 1
                    )[1]
                    if os.path.isfile(
                        os.path.normpath(game_directory + os.path.sep + relative_path)
                    ):
                        if bool(
                            self._organizer.pluginSetting(
                                ARCExtract.name(ARCExtract), "verbose-log"
                            )
                        ):
                            log_out += f"ARC Folder: {full_path}\n"
                        if bool(
                            self._organizer.pluginSetting(
                                ARCExtract.name(ARCExtract), "merge-mode"
                            )
                        ):
                            ARCExtract.arc_files_seen_dict[relative_path].append(
                                mod_name
                            )
                        if any(
                            relative_path in x for x in ARCExtract.arc_files_seen_dict
                        ):
                            mod_where_first_seen = ARCExtract.arc_files_seen_dict[
                                relative_path
                            ][0]
                            ARCExtract.arc_files_duplicate_dict[relative_path].append(
                                mod_where_first_seen
                            )
                            if (
                                mod_name
                                not in ARCExtract.arc_files_duplicate_dict[
                                    relative_path
                                ]
                            ):
                                ARCExtract.arc_files_duplicate_dict[
                                    relative_path
                                ].append(mod_name)
                        else:
                            if (
                                mod_name
                                not in ARCExtract.arc_files_seen_dict[relative_path]
                            ):
                                ARCExtract.arc_files_seen_dict[relative_path].append(
                                    mod_name
                                )
                # check for arc files
                for file in filenames:
                    if file.endswith(".arc"):
                        full_path = dirpath + os.path.sep + file
                        relative_path = os.path.relpath(full_path, mod_directory).split(
                            os.path.sep, 1
                        )[1]
                        if bool(
                            self._organizer.pluginSetting(
                                ARCExtract.name(ARCExtract), "merge-mode"
                            )
                        ):
                            if (
                                mod_name
                                not in ARCExtract.arc_files_seen_dict[relative_path]
                            ):
                                ARCExtract.arc_files_seen_dict[relative_path].append(
                                    mod_name
                                )
                        if any(
                            relative_path in x for x in ARCExtract.arc_files_seen_dict
                        ):
                            mod_where_first_seen = ARCExtract.arc_files_seen_dict[
                                relative_path
                            ][0]
                            ARCExtract.arc_files_duplicate_dict[relative_path].append(
                                mod_where_first_seen
                            )
                            log_out += f"Duplicate ARC: {os.path.join(dirpath, file)}\n"
                            if (
                                mod_name
                                not in ARCExtract.arc_files_duplicate_dict[
                                    relative_path
                                ]
                            ):
                                ARCExtract.arc_files_duplicate_dict[
                                    relative_path
                                ].append(mod_name)
                        else:
                            if (
                                mod_name
                                not in ARCExtract.arc_files_seen_dict[relative_path]
                            ):
                                ARCExtract.arc_files_seen_dict[relative_path].append(
                                    mod_name
                                )
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
        executable = self._organizer.pluginSetting(
            ARCExtract.name(ARCExtract), "ARCTool-path"
        )
        executable_path, executable_name = os.path.split(executable)
        arc_file_parent_relpath = os.path.dirname(self._arc_file)
        arc_file_fullpath = os.path.join(executable_path, self._arc_file)
        extracted_arc_folder_relpath = os.path.splitext(self._arc_file)[0]
        game_directory = self._organizer.managedGame().dataDirectory().absolutePath()
        mod_directory = self._organizer.modsPath()
        log_out = "\n"
        # extract vanilla if needed
        extracted_arc_folder_fullpath = os.path.join(
            executable_path, extracted_arc_folder_relpath
        )
        if not os.path.isdir(extracted_arc_folder_fullpath):
            log_out += f"Extracting vanilla ARC: {self._arc_file}\n"
            if os.path.isfile(os.path.join(game_directory, self._arc_file)):
                pathlib.Path(f"{executable_path}\{arc_file_parent_relpath}").mkdir(
                    parents=True, exist_ok=True
                )
                shutil.copy(
                    os.path.join(game_directory, self._arc_file),
                    os.path.join(executable_path, arc_file_parent_relpath),
                )
                command_out = os.popen(
                    f'{executable} {args} "{arc_file_fullpath}"'
                ).read()
                if bool(
                    self._organizer.pluginSetting(
                        ARCExtract.name(ARCExtract), "verbose-log"
                    )
                ):
                    log_out += "------ start arctool output ------\n"
                    log_out += command_out + "------ end arctool output ------\n"
                # remove .arc file
                os.remove(arc_file_fullpath)
            else:
                # no matching vanilla file to extract
                self.signals.finished.emit()  # Done
                return
        for mod_name in self._mod_list:
            arc_fullpath = os.path.join(mod_directory, mod_name, self._arc_file)
            if os.path.isfile(arc_fullpath):
                log_out += f"Extracting: {mod_name} {self._arc_file}\n"
                # extract arc and remove ITM
                command_out = os.popen(f'{executable} {args} "{arc_fullpath}"').read()
                if bool(
                    self._organizer.pluginSetting(
                        ARCExtract.name(ARCExtract), "verbose-log"
                    )
                ):
                    log_out += "------ start arctool output ------\n"
                    log_out += command_out + "------ end arctool output ------\n"
                # remove ITM
                if bool(self._organizer.pluginSetting("ARC Extract", "remove-ITM")):
                    log_out += "Removing ITM\n"

                    def delete_same_files(dcmp):
                        for name in dcmp.same_files:
                            os.remove(os.path.join(dcmp.right, name))
                        for sub_dcmp in dcmp.subdirs.values():
                            delete_same_files(sub_dcmp)

                    dcmp = filecmp.dircmp(
                        os.path.join(executable_path, extracted_arc_folder_relpath),
                        os.path.join(
                            mod_directory, mod_name, extracted_arc_folder_relpath
                        ),
                    )
                    delete_same_files(dcmp)
                    # delete empty folders
                    for dirpath, dirnames, filenames in os.walk(
                        os.path.join(
                            mod_directory, mod_name, extracted_arc_folder_relpath
                        ),
                        topdown=False,
                    ):
                        for dirname in dirnames:
                            full_path = os.path.join(dirpath, dirname)
                            if not os.listdir(full_path):
                                os.rmdir(full_path)
                                pathlib.Path(f"{full_path}.arc.txt").unlink(
                                    missing_ok=True
                                )
                # delete arc
                if bool(
                    self._organizer.pluginSetting(
                        ARCExtract.name(ARCExtract), "delete-ARC"
                    )
                ):
                    log_out += f"Deleting {arc_fullpath}\n"
                    pathlib.Path(arc_fullpath).unlink(missing_ok=True)
                # remove .arc.txt
                if not bool(
                    self._organizer.pluginSetting(
                        ARCExtract.name(ARCExtract), "merge-mode"
                    )
                ):
                    pathlib.Path(os.path.join(arc_fullpath, ".txt")).unlink(
                        missing_ok=True
                    )
                log_out += "ARC extract complete"
        if log_out != "\n":
            self.signals.result.emit(log_out)  # Return log
        self.signals.finished.emit()  # Done
        return


def createPlugin():
    return ARCExtract()
