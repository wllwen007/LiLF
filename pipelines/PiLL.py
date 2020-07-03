#!/usr/bin/env python

import os, sys, glob
from surveys_db import SurveysDB
from LiLF import lib_util
parset = lib_util.getParset(parsetFile='lilf.config')
LiLF_dir = os.path.dirname(lib_util.__file__)

w = lib_util.Walker('PiLL.walker')

survey_projects = 'LT14_002,LC12_017,LC9_016,LC8_031' # list of projects related with the LBA survey

# get parameters
# use lilf.config (this is also used by all other scripits)
working_dir = os.path.abspath(parset.get('PiLL','working_dir'))
redo_cal = parset.getboolean('PiLL','redo_cal')
project = parset.get('PiLL','project')
target = parset.get('PiLL','target')
download_file = parset.get('PiLL','download_file')
if download_file != '': download_file =os.path.abspath(download_file)

def calibrator_tables_available(obsid):
    """
    check if calibrator data exist in the database
    """
    with SurveysDB(survey='lba',readonly=True) as sdb:
        sdb.execute('select * from observations where id=%f' % obsid)
        r = sdb.cur.fetchall()
        if len(r) == 0: return False
        if r[0]['location'] != '': return True


def local_calibrator_dirs(searchdir='', obsid=None):
    """
    Return the dirname of the calibrators
    """
    if searchdir != '': searchdir += '/'
    if obsid is None:
        calibrators = glob.glob(searchdir+'id*_3[C|c]196') + \
                  glob.glob(searchdir+'id*_3[C|c]295') + \
                  glob.glob(searchdir+'id*_3[C|c]380')
    else:
        calibrators = glob.glob(searchdir+'/id%i_3[C|c]196' % obsid) + \
                  glob.glob(searchdir+'id%i_3[C|c]295' % obsid) + \
                  glob.glob(searchdir+'id%i_3[C|c]380' % obsid)

    if len(calibrators) == 0: return None
    else: return calibrators


def update_status_db(field, status):
    with SurveysDB(survey='lba',readonly=True) as sdb:
        r = sdb.execute('update fields set status=%s where id=%f' % (status,field))


####################################################################################

# query the database for data to process
if download_file == '' and project == '' and target == '':
    project = survey_projects
    print('### Quering database...')
    with SurveysDB(survey='lba',readonly=True) as sdb:
        sdb.execute('select * from fields where status="Observed" order by priority desc')
        r = sdb.cur.fetchall()
        target = r[0]['id']
    print("Working on target: %s" % target)

#######
# setup
if not os.path.exists(working_dir):
    os.makedirs(working_dir)
if os.path.exists('lilf.config') and os.getcwd() != working_dir: 
    os.system('cp lilf.config '+working_dir)

os.chdir(working_dir)
if not os.path.exists('download'):
    os.makedirs('download')

if download_file != '':
    os.system('cp %s download/html.txt' % download_file)

##########
# data download
if w.todo('download'):
    os.chdir(working_dir+'/download')

    if download_file == '':
        cmd = LiLF_dir+'/scripts/LOFAR_stager.py --projects %s --nocal' % project
        if target != '':
            cmd += ' --target %s' % target
        print("### Exec:", cmd)
        os.system(cmd)

    # TODO: how to be sure all MS were downloaded?
    os.system(LiLF_dir+'/pipelines/LOFAR_download.py')

    os.chdir(working_dir)
    os.system('mv download/mss/* ./')
    
    w.done('download')
### DONE

calibrators = local_calibrator_dirs()
targets = [t for t in glob.glob('id*') if t not in calibrators]
print ('CALIBRATORS:', calibrators)
print ('TARGET:', targets)

for target in targets:
    
    ##########
    # calibrator
    obsid = int(target.split('_')[0][2:])
    if w.todo('cal_id%i' % obsid):
        if redo_cal or not calibrator_tables_available(obsid):
            # if calibrator not downaloaded, do it
            cal_dir = local_calibrator_dirs(working_dir, obsid)
        
            if cal_dir is None:
                os.chdir(working_dir+'/download')
                os.system(LiLF_dir+'/scripts/LOFAR_stager.py --cal --projects %s --obsid %i' % (project, obsid))
                os.system(LiLF_dir+'/pipelines/LOFAR_download.py')
    
                calibrator = local_calibrator_dirs('./mss/', obsid)[0]
                os.system('mv '+calibrator+' '+working_dir)

            os.chdir(local_calibrator_dirs(working_dir, obsid)[0])
            if not os.path.exists('data-bkp'):
                os.makedirs('data-bkp')
                os.system('mv *MS data-bkp')
            os.system(LiLF_dir+'/pipelines/LOFAR_cal.py')
    
        w.done('cal_id%i' % obsid)
    ### DONE

    ##########
    # timesplit
    if w.todo('timesplit_%s' % target):
        os.chdir(working_dir+'/'+target)
        if not os.path.exists('data-bkp'):
            os.makedirs('data-bkp')
            os.system('mv *MS data-bkp')

        os.system(LiLF_dir+'/pipelines/LOFAR_timesplit.py')

        w.done('timesplit_%s' % target)
    ### DONE

# group targets with same name, assuming they are different pointings of the same dir
grouped_targets = set([t.split('_')[1] for t in targets])

for grouped_target in grouped_targets:
    if not os.path.exists(working_dir+'/'+grouped_target):
        os.makedirs(working_dir+'/'+grouped_target)
    os.chdir(working_dir+'/'+grouped_target)
    
    # collet mss
    if not os.path.exists('mss'):
        os.makedirs('mss')
        for i, tc in enumerate(glob.glob('../id*_'+grouped_target+'/mss/TC*MS')):
            tc_ren = 'TC%02i.MS' % i
            print('mv %s mss/%s' % (tc,tc_ren))
            os.system('mv %s mss/%s' % (tc,tc_ren))

    ##########
    # selfcal
    if w.todo('self_%s' % grouped_target):
        update_status_db(grouped_target, 'Self')
        os.system(LiLF_dir+'/pipelines/LOFAR_self.py')
        w.done('self_%s' % grouped_target)
    ### DONE

    ##########
    # DD-cal
    if w.todo('dd_%s' % grouped_target):
        update_status_db(grouped_target, 'Ddcal')
        os.system(LiLF_dir+'/pipelines/LOFAR_dd-serial.py')
        w.done('dd_%s' % grouped_target)
    ### DONE

    # TODO: add error status
    update_status_db(grouped_target, 'Done')

