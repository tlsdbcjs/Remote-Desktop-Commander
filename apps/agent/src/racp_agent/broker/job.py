"""A named Broker Job: controller management, user query, and kill-on-close lifetime."""

from typing import Any

from racp_agent.broker.config import BrokerConfig


def create_broker_job(config: BrokerConfig) -> Any:
    import pywintypes
    import win32api
    import win32job
    import win32security

    config.job_name = "Global\\RACP-Broker-Job-" + config.pair_id
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    descriptor = f"D:P(A;;GA;;;{config.agent_acl_sid})(A;;GA;;;SY)"
    if config.user_sid != config.agent_acl_sid:
        descriptor += f"(A;;0x4;;;{config.user_sid})"  # JOB_OBJECT_QUERY only.
    attributes.SECURITY_DESCRIPTOR = (
        win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(descriptor, 1)
    )
    job = win32job.CreateJobObject(attributes, config.job_name)
    try:
        if win32api.GetLastError() == 183:
            raise PermissionError("Broker Job name already exists")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        return job
    except BaseException:
        job.Close()
        raise
