import boto3
from retrying import retry
import time

client = boto3.client('elasticbeanstalk')

environments_config = {
    'Application': 'eb-app-name',
    'GreenURL': 'app-green.us-east-1.elasticbeanstalk.com',
    'Environment-1': 'app-1',
    'Environment-2': 'app-2',
}


def is_false(result):
    return result is False


@retry(wait_fixed=5000, stop_max_attempt_number=60, retry_on_result=is_false, wrap_exception=True)
def is_environment_ready(env_name):
    checked_env = client.describe_environments(ApplicationName=environments_config['Application'], EnvironmentNames=[env_name])
    env_status = checked_env['Environments'][0]['Status'] == 'Ready'
    if env_status:
        print('Environment {} is ready.'.format(env_name))
        return True
    else:
        return False


def who_is_green():
    api_environments = client.describe_environments(ApplicationName=environments_config['Application'])
    for environment in api_environments['Environments']:
        if environment['CNAME'] == environments_config['GreenURL']:
            return environment['EnvironmentName']


def get_environments_config(env_name):
    return client.describe_configuration_settings(ApplicationName=environments_config['Application'], EnvironmentName=env_name,)


def get_asg_settings(options):
    asg_settings = {}
    for option in options['ConfigurationSettings'][0]['OptionSettings']:
        if option['Namespace'] == 'aws:autoscaling:asg':
            asg_settings[option['OptionName']] = option['Value']
    return asg_settings


def get_current_in_service(environment):
    return client.describe_environment_health(
        EnvironmentName=environment, AttributeNames=['InstancesHealth'])['InstancesHealth']['Ok']


def get_not_green(green):
    if environments_config['Environment-1'] == green:
        return environments_config['Environment-2']
    else:
        return environments_config['Environment-1']


def update_asg_settings(environment, minvalue):
    print('Updating environment\'s {} ASG MinValue to {}'.format(environment, minvalue))
    client.update_environment(EnvironmentName=environment, OptionSettings=[
        {'Namespace': 'aws:autoscaling:asg', 'OptionName': 'MinSize', 'Value': minvalue}])


oldGreen = ''
oldGreen = who_is_green()
newGreen = get_not_green(oldGreen)
if len(oldGreen) == 0:
    print('Something wrong - no environment is green.')
    exit(254)

print('Current GREEN environment is: {}'. format(oldGreen))

print('Checking if environment is ready...')
if not is_environment_ready(oldGreen):
    print('Environment is not ready, can not proceed.')
    exit(1)

oldAsgSettings = get_asg_settings(get_environments_config(oldGreen))
newAsgSettings = get_asg_settings(get_environments_config(newGreen))
currentInService = str(get_current_in_service(oldGreen))

print('Matching new environment\'s capacity. Currently serving: {}'.format(currentInService))
print('Green environment ASG min setting: {}'.format(oldAsgSettings['MinSize']))
print('Blue environment ASG min setting: {}'.format(newAsgSettings['MinSize']))

updateRequired = False
if newAsgSettings['MinSize'] != currentInService:
    updateRequired = True
    update_asg_settings(newGreen, currentInService)
    print('Checking if environment is ready after ASG update...')
    if not is_environment_ready(newGreen):
        print('Environment is not ready, can not proceed.')
else:
    print('Currently in service matches min size. No update required')

print('Performing environment swap...')

response = client.swap_environment_cnames(
    DestinationEnvironmentName=environments_config['Environment-1'],
    SourceEnvironmentName=environments_config['Environment-2'],
)

if response['ResponseMetadata']['HTTPStatusCode'] != 200:
    print('Request to swap environments failed: {}'.format(response))
    exit(2)
else:
    print('Request to swap environments was successful.')

print('Checking if OLD environment becomes ready...')
if not is_environment_ready(oldGreen):
    print('Environment {} did not become ready after 30 seconds. Please check manually in AWS console.'.format(oldGreen))
    exit(3)

print('New GREEN environment is: {}'. format(who_is_green()))

if updateRequired:
    print('Sleeping for 60 seconds and initiating update of ASG min value...')
    time.sleep(60)
    print('Updating new environment setting min ASG value back to previous value.')
    update_asg_settings(newGreen, newAsgSettings['MinSize'])
    if not is_environment_ready(newGreen):
        print('Environment did not become ready. Please check environment status in AWS console.')
        exit(1)
