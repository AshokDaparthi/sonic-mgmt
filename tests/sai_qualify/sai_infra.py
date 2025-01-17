"""
    SAI testing test bed setup.

    Notes:
        This test is used to setup the SAI testing environment, and start the SAI test cases
        from the PTF.
        For running this tests, please specify the sai test case folder via the parameters --sai_test_folder.

"""

import pytest, socket, sys, itertools, logging
import ptf.packet as scapy
import tests.common.system_utils.docker as docker
import tests.common.fixtures.ptfhost_utils as ptfhost_utils
import time

from conftest import *
from community_cases import *

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.topology("ptf")
]

TEST_INTERFACE_PARAMS = "--interface '0@eth0' --interface '1@eth1' --interface '2@eth2' \
--interface '3@eth3' --interface '4@eth4' --interface '5@eth5' --interface '6@eth6' \
--interface '7@eth7'"
SAI_TEST_ENV_RESET_TIMES = 3
LIVENESS_CHECK_RETRY_TIMES = 12
LIVENESS_CHECK_INTERVAL_IN_SEC = 5
CONFIG_RELOAD_INTERVAL_IN_SEC = 30
TEST_INTERVAL_IN_SEC = 1


@pytest.mark.parametrize("test_case", COMMUN_TEST_CASE)
def test_sai_from_ptf(
    sai_testbed, sai_test_env_check, creds, duthost, ptfhost, test_case, request):
    """
        trigger the test here
    """
    dut_ip = duthost.host.options['inventory_manager'].get_host(duthost.hostname).vars['ansible_host']
    try:
        run_case_from_ptf(dut_ip, ptfhost, test_case)
    except BaseException as e:
        logger.info("Test case [{}] failed, trying to restart sai test container, failed as {}.".format(test_case, e))               
        stop_and_rm_sai_test_container(duthost, get_sai_test_container_name(request))        
        pytest.fail("Test case [{}] failed".format(test_case), e)
    finally:
        _store_test_result(ptfhost)

@pytest.fixture
def sai_test_env_check(creds, duthost, ptfhost, request):
    """
        Check the sai test environment.
        In this function, it will make a liveness check test to check if the sai test container is ready for test.
        This check has three stage:
        1. If the liveness check test failed, then it will make a environment reset.
        2. If the envvironment reset failed with attempts, then the test environment will be marked as failed.
        3. If environment marked as failed, this check will be failed in following round of check. 
    """
    global IS_TEST_ENV_FAILED
    if IS_TEST_ENV_FAILED:
        logger.info("Test env check is failed in previous check. Fails this check directly.")
        raise Exception("SAI Test env error.")

    for retry in range(SAI_TEST_ENV_RESET_TIMES):
        try:
            sai_test_container_liveness_check(duthost, ptfhost, PROBE_TEST_CASE, request)
            break
        except BaseException as e:  
            logger.info("Run test env check failed, reset the env, retry: [{}/{}], failed as {}.".format(retry + 1, SAI_TEST_ENV_RESET_TIMES, e))
            if retry + 1 < SAI_TEST_ENV_RESET_TIMES:
                reset_sai_test_dut(duthost, creds, request)
                logger.info("Liveness check waiting {} sec for another retry.".format(LIVENESS_CHECK_INTERVAL_IN_SEC))
                time.sleep(LIVENESS_CHECK_INTERVAL_IN_SEC)
            else:
                logger.info("Run test env check failed. Run test env is not ready. Error: {}".format(e))
                IS_TEST_ENV_FAILED = True
                raise e


@pytest.fixture(scope="module")
def sai_testbed(
    duthost,
    request,
    ptfhost,
    start_sai_test_container,
    prepare_ptf_server):
    """
        Pytest fixture to handle setup and cleanup for the SAI tests.
    """
    try:        
        _setup_dut(ptfhost, request)
        yield  
    finally:  
        _store_test_result(ptfhost)
        _teardown_dut(duthost, ptfhost, request)


def run_case_from_ptf(dut_ip, ptfhost, test_case):
    """
    Run the sai test cases from ptf.
    Args:
        dut_ip: dut ip address.
        ptfhost (AnsibleHost): The PTF server.
        test_case: Test case name used to make test.
    """
    logger.info("Running test: {0}".format(test_case))
    logger.info("Sleep {} sec between tests.".format(TEST_INTERVAL_IN_SEC))
    time.sleep(TEST_INTERVAL_IN_SEC)
    ptfhost.shell(("ptf --test-dir {0} {1} {2} --relax --xunit --xunit-dir {3} " + 
        "-t \"server='{4}';port_map_file='{5}'\"")
    .format(
        SAI_TEST_CASE_DIR_ON_PTF, 
        test_case, 
        TEST_INTERFACE_PARAMS,
        SAI_TEST_REPORT_TMP_DIR_ON_PTF, 
        dut_ip, 
        PORT_MAP_FILE_PATH))
    logger.info("Test case [{}] passed.".format(test_case))


def reset_sai_test_dut(duthost, creds, request):
    """
        Resets the sai test environment.
        This function will remove all the sai test container, reload config, re_deploy sai test container and start them.
    Args:
        duthost (SonicHost): The target device.
        creds (dict): Credentials used to access the docker registry.
        request: Pytest request. 
    """
    logger.info("Start to reset dut environment to default.")
    stop_and_rm_sai_test_container(duthost, get_sai_test_container_name(request))
    revert_sai_test_container(duthost, creds, get_sai_test_container_name(request))    
    reload_dut_config(duthost)
    logger.info("Resetting Dut env, waiting {} sec for env gets ready ...".format(CONFIG_RELOAD_INTERVAL_IN_SEC))
    time.sleep(CONFIG_RELOAD_INTERVAL_IN_SEC)
    stop_dockers(duthost)
    prepare_sai_test_container(duthost, creds, get_sai_test_container_name(request))
    start_sai_test_conatiner_with_retry(duthost, get_sai_test_container_name(request))


def sai_test_container_liveness_check(duthost, ptfhost, test_case, request):
    """
        Run a liveness check.
        This function will run a simple test to check if the sai test container is ready.
    Args:
        duthost (SonicHost): The target device.        
        ptfhost (AnsibleHost): The PTF server.
        test_case: Test case name used to make the liveness check.
        request: Pytest request. 
    """
    logger.info("Checking test environment before running test.")
    dut_ip = duthost.host.options['inventory_manager'].get_host(duthost.hostname).vars['ansible_host']
    start_sai_test_conatiner_with_retry(duthost, get_sai_test_container_name(request))
    for retry in range(LIVENESS_CHECK_RETRY_TIMES):
        try:
            run_case_from_ptf(dut_ip, ptfhost, test_case)
            break
        except BaseException as e:  
            logger.info("Run liveness check [{}], retry: [{}/{}] failed as {}".format(test_case, retry + 1, LIVENESS_CHECK_RETRY_TIMES,  e))
            if retry + 1 < LIVENESS_CHECK_RETRY_TIMES:
                logger.info("Liveness check waiting {} sec for another retry.".format(LIVENESS_CHECK_INTERVAL_IN_SEC))
                time.sleep(LIVENESS_CHECK_INTERVAL_IN_SEC)
            else:
                logger.info("Liveness check failed. TestBed is not ready. Error: {}".format(e))
                raise e


def _setup_dut(ptfhost, request):
    """
        Sets up the SAI tests.
    Args:
        ptfhost (AnsibleHost): The PTF server.
        request: Pytest request. 
    """
    logger.info("Set up SAI tests.")

    _prepare_test_cases(ptfhost, request)


def _teardown_dut(duthost, ptfhost, request):
    """
        Tears down the SAI test.
    """
    logger.info("Teardown SAI tests.")
    _collect_test_result(duthost, ptfhost, request)
    _cleanup_ptf(ptfhost)


def _cleanup_ptf(ptfhost):
    """
    Cleanup PTF server, including delete test cases and root test folder.

    Args:
        ptfhost (AnsibleHost): The PTF server.
    """
    _delete_sai_test_cases(ptfhost)
    _delete_sai_test_folder(ptfhost)


def _delete_sai_test_cases(ptfhost):
    """
    Delete SAI test cases on PTF.

    Args:
        ptfhost (AnsibleHost): The PTF server.
    """
    logger.info("Delete SAI tests cases")
    ptfhost.file(path="{0}".format(SAI_TEST_CASE_DIR_ON_PTF), state="absent")


def _delete_sai_test_folder(ptfhost):
    """
    Delete SAI test root folder on PTF.

    Args:
        ptfhost (AnsibleHost): The PTF server.
    """
    logger.info("Delete SAI tests root folder: {0}.".format(PTF_TEST_ROOT_DIR))
    ptfhost.file(path=PTF_TEST_ROOT_DIR, state="absent")


def _prepare_test_cases(ptfhost, request):
    """
    Prepare SAI test env including create root test folder and copy cases.

    Args:
        ptfhost (AnsibleHost): The PTF server.
    """
    logger.info("Preparing SAI test environment.")
    _create_sai_test_folders(ptfhost)
    _copy_sai_test_cases(ptfhost, request)


def _create_sai_test_folders(ptfhost):
    """
    Create SAI test root folder on PTF server.

    Args:
        ptfhost (AnsibleHost): The PTF server.
    """
    logger.info("Creating SAI tests root folder: {0}.".format(PTF_TEST_ROOT_DIR))
    ptfhost.shell("mkdir -p {0}".format(PTF_TEST_ROOT_DIR))
    logger.info("Creating SAI tests report folder: {0}.".format(SAI_TEST_REPORT_DIR_ON_PTF))
    ptfhost.shell("mkdir -p {0}".format(SAI_TEST_REPORT_DIR_ON_PTF))


def _copy_sai_test_cases(ptfhost, request):
    """
    Copy SAI test cases to PTF server.

    Args:
        ptfhost (AnsibleHost): The PTF server.
    """
    logger.info("Copying SAI test cases to PTF server.")
    ptfhost.copy(src=request.config.option.sai_test_dir, dest=PTF_TEST_ROOT_DIR + "/")


def _collect_test_result(duthost, ptfhost, request):
    """
    Collect SAI test resport from DUT and PTF server.

    Args:
        duthost (SonicHost): The DUT.
        ptfhost (AnsibleHost): The PTF server.
    """
    logger.info("Collecting test result and related information.")
    # TODO : collect DUT test report
    _collect_sonic_os_and_platform_info(duthost, request)
    _collect_sai_test_report_xml(ptfhost, request)


def _collect_sonic_os_and_platform_info(duthost, request):
    """
    Collect SONiC OS and Testbed info.

    Args:
        duthost (SonicHost): The DUT.
    """
    logger.info("Getting SONiC OS version and Testbed platform info.")

    out = duthost.shell("cd {0} && show version".format(DUT_WORKING_DIR))
    _parse_info(out['stdout'], request.config.option.sai_test_report_dir)


def _parse_info(content, report_path):
    OS_VERSION=""
    PLT_VERSION=""

    with open(report_path + "/version.txt", 'w+') as f:
        f.writelines(content)

    with open(report_path + "/version.txt", 'r') as f:
        cc = f.readlines()

        for line in cc:
            if "SONiC Software Version" in line:
                OS_VERSION = line.split(":")[1].strip()
            if "Platform" in line:
                PLT_VERSION = line.split(":")[1].strip()

    # TODO: Getting info should not depend on AZP, later this logging command will be removed
    logger.info('Get SONiC version: {0}, Platform: {1}'.format(OS_VERSION, PLT_VERSION))
    logger.info('##vso[task.setvariable variable=OS_VERSION]{}'.format(OS_VERSION))
    logger.info('##vso[task.setvariable variable=PLT_VERSION]{}'.format(PLT_VERSION))


def _store_test_result(ptfhost):
    """
        Backup the test result
    
    Args:
        ptfhost (AnsibleHost): The PTF server.
    """
    logger.info("Copying file from folder: {0} to folder: {1}".format(
		SAI_TEST_REPORT_TMP_DIR_ON_PTF, 
		SAI_TEST_REPORT_DIR_ON_PTF))
    ptfhost.shell("cp {0}/*.* {1}/".format(
		SAI_TEST_REPORT_TMP_DIR_ON_PTF, 
		SAI_TEST_REPORT_DIR_ON_PTF))


def _collect_sai_test_report_xml(ptfhost, request):
    """
    Collect SAI test report.

    Args:
        ptfhost (AnsibleHost): The PTF server.
    """
    logger.info("Collecting xunit SAI tests log from ptf")
    ptfhost.shell("cd {0} && tar -czvf result.tar.gz *".format(SAI_TEST_REPORT_DIR_ON_PTF))
    ptfhost.fetch(
        src="{0}/result.tar.gz".format(SAI_TEST_REPORT_DIR_ON_PTF), 
        dest=request.config.option.sai_test_report_dir + "/", 
        flat=True)

