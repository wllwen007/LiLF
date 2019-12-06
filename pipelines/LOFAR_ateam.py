#!/usr/bin/env python
# -*- coding: utf-8 -*-

# initial calibration of the calibrator in circular, get and corr FR, back to linear, sol flag + effects separation

import sys, os, glob, re
import numpy as np

def rev_reg(filename, region):
    """
    reverse region negative values

    filename: fits file
    region: ds9 region or list of regions
    """
    import astropy.io.fits as pyfits
    import pyregion

    # open fits
    with pyfits.open(filename) as fits:
        origshape    = fits[0].data.shape
        header, data = lib_img.flatten(fits)
        
        # extract mask
        r = pyregion.open(region)
        mask = r.get_mask(header=header, shape=data.shape)
        data[mask & (data<0)] *= -1
        # save fits
        fits[0].data = data.reshape(origshape)
        fits.writeto(filename, overwrite=True)

    logger.debug("%s: Reversing pixels (%s)" % (filename, region))

if 'Vir' in os.getcwd():
    patch = 'VirA'
    nouseblrange = ''
    #f = lambda nu: 1226. * 10**(-0.79 * (np.log10(nu/150.e6))**1)
    f = lambda nu: 10**(2.4466 - 0.8116 * ((np.log10(nu/1.e9))**1) - 0.0483 * ((np.log10(nu/1.e9))**2) ) # PB17
elif 'Tau' in os.getcwd():
    patch = 'TauA'
    nouseblrange = '' #'[500..5000]' # below is a point, above 10 times is hopefully resolved out
    #f = lambda nu: 1838. * 10**(-0.299 * (np.log10(nu/150.e6))**1)
    f = lambda nu: 10**(2.9516 - 0.2173 * ((np.log10(nu/1.e9))**1) - 0.0473 * ((np.log10(nu/1.e9))**2) - 0.0674 * ((np.log10(nu/1.e9))**3)) # PB17
elif 'Cas' in os.getcwd():
    patch = 'CasA'
    nouseblrange = '' #'[15000..1e30]'
    #f = lambda nu: 11733. * 10**(-0.77 * (np.log10(nu/150.e6))**1)
    f = lambda nu: 10**(3.3584 - 0.7518 * ((np.log10(nu/1.e9))**1) - 0.0347 * ((np.log10(nu/1.e9))**2) - 0.0705 * ((np.log10(nu/1.e9))**3)) # PB17
elif 'Cyg' in os.getcwd():
    patch = 'CygA'
    nouseblrange = ''
    #f = lambda nu: 10690. * 10**(-0.67 * (np.log10(nu/150.e6))**1) * 10**(-0.204 * (np.log10(nu/150.e6))**2) * 10**(-0.021 * (np.log10(nu/150.e6))**3)
    f = lambda nu: 10**(3.3498 - 1.0022 * ((np.log10(nu/1.e9))**1) - 0.2246 * ((np.log10(nu/1.e9))**2) + 0.0227 * ((np.log10(nu/1.e9))**3) + 0.0425 * ((np.log10(nu/1.e9))**4)) # PB17

skymodel = '/home/fdg/scripts/model/A-team_4_CC.skydb'

########################################################
from LiLF import lib_ms, lib_img, lib_util, lib_log
logger_obj = lib_log.Logger('pipeline-ateam.logger')
logger = lib_log.logger
s = lib_util.Scheduler(log_dir = logger_obj.log_dir, dry = False)

# parse parset
parset = lib_util.getParset()
parset_dir = parset.get('LOFAR_ateam','parset_dir')
bl2flag = parset.get('flag','stations')
data_dir = '../tgts-bkp/'

##########################################################
logger.info('Cleaning...')
lib_util.check_rm('cal*h5')
lib_util.check_rm('plots*')
lib_util.check_rm('img')
os.makedirs('img')
MSs = lib_ms.AllMSs( sorted(glob.glob(data_dir+'/*MS')), s )

# copy data (avg to 1ch/sb and 10 sec)
nchan = int(MSs.getListObj()[0].getNchan()) # add /4. to have more channels
timeint = MSs.getListObj()[0].getTimeInt()
avg_time = int(np.rint(10./timeint)) # change 10. to a lower number to have more times

logger.info('Copy data...')
for obs in set([ os.path.basename(ms).split('_')[0] for ms in MSs.getListStr() ]):
    mss_toconcat = glob.glob(data_dir+'/'+obs+'*MS')
    MS_concat = obs+'_concat.MS'
    MS_concat_bkp = obs+'_concat.MS-bkp'
    if os.path.exists(MS_concat_bkp): 
        os.system('rm -r %s' % MS_concat)
        os.system('cp -r %s %s' % (MS_concat_bkp, MS_concat) )
    else:
        s.add('DPPP '+parset_dir+'/DPPP-avg.parset msin=\"'+str(mss_toconcat)+'\" msout='+MS_concat+' avg.freqstep=%i avg.timestep=%i' % (nchan, avg_time),\
            log=obs+'_avg.log', commandType='DPPP')
s.run(check=True, maxThreads=2)

################################################################
MSs = lib_ms.AllMSs( glob.glob('*MS'), s )

# bkp
for MS in MSs.getListStr():
    MS_bkp = MS+'-bkp'
    if not os.path.exists(MS_bkp):
        logger.info('Making backup...')
        os.system('cp -r %s %s' % (MS, MS_bkp) ) # do not use MS.move here as it resets the MS path to the moved one

# HBA/LBA
if min(MSs.getFreqs()) < 80.e6:
    lofar_system = 'lba'
    flag_steps = "[ant, uvmin, elev, count]"
else: 
    lofar_system = 'hba'
    flag_steps = "[ears, ant, uvmin, elev, count]"

########################################################   
# flag bad stations, and low-elev
logger.info('Flagging...')
MSs.run('DPPP '+parset_dir+'/DPPP-flag.parset msin=$pathMS msout=. steps=\"'+flag_steps+'\" ant.baseline=\"'+bl2flag+'\"', \
            log='$nameMS_flag.log', commandType='DPPP')

if lofar_system == 'hba': model_dir = '/home/fdg/scripts/model/AteamHBA/'+patch
else: model_dir = '/home/fdg/scripts/model/AteamLBA/'+patch

if os.path.exists(model_dir+'/img-MFS-model.fits'):
    im = lib_img.Image(model_dir+'/img-MFS-image.fits')
    im.rescaleModel(f)
    n = len(glob.glob(model_dir+'/img-[0-9]*-model.fits'))
    logger.info('Predict (wsclean: %s - chan: %i)...' % (model_dir, n))
    s.add('wsclean -predict -name '+model_dir+'/img -j '+str(s.max_processors)+' -channels-out '+str(n)+' '+MSs.getStrWsclean(), \
          log='wscleanPRE-init.log', commandType='wsclean', processors='max')
    s.run(check=True)
else:
    logger.info('Predict (DPPP)...')
    MSs.run('DPPP '+parset_dir+'/DPPP-predict.parset msin=$pathMS pre.sourcedb='+skymodel+' pre.sources='+patch, log='$nameMS_pre.log', commandType='DPPP')

# TESTTESTTEST
# BL Smooth DATA -> DATA
#logger.info('BL-based smoothing...')
#MSs.run('BLsmooth.py -r -i DATA -o DATA $pathMS', log='$nameMS_smooth.log', commandType='python')

for c in range(100):

    logger.info('== Start cycle: %s ==' % c)

    #logger.info('Remove bad timestamps...')
    #MSs.run( 'flagonmindata.py -f 0.5 $pathMS', log='$nameMS_flagonmindata.log', commandType='python')

    ####################################################
    # 1: find PA and remove it

    # Solve cal_SB.MS:DATA (only solve)
    logger.info('Solving PA...')
    MSs.run('DPPP ' + parset_dir + '/DPPP-soldd.parset msin=$pathMS msin.datacolumn=DATA sol.h5parm=$pathMS/pa.h5 sol.mode=rotation+diagonal \
            sol.uvlambdarange='+str(nouseblrange), log='$nameMS_solPA.log', commandType="DPPP")

    lib_util.run_losoto(s, 'pa-c'+str(c), [ms+'/pa.h5' for ms in MSs.getListStr()], \
                    [parset_dir+'/losoto-plot-ph.parset', parset_dir+'/losoto-plot-rot.parset', parset_dir+'/losoto-plot-amp.parset', parset_dir+'/losoto-pa.parset'])

    #################################################
    # 2: find the FR and remve it
    
    # Beam correction DATA -> CORRECTED_DATA
    logger.info('PA correction...')
    MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS msin.datacolumn=DATA cor.parmdb=cal-pa-c'+str(c)+'.h5 cor.correction=polalign', \
            log='$nameMS_corPA2.log', commandType="DPPP")

    # Beam correction CORRECTED_DATA -> CORRECTED_DATA
    logger.info('Beam correction...')
    MSs.run('DPPP '+parset_dir+'/DPPP-beam.parset msin=$pathMS', log='$nameMS_beam2.log', commandType='DPPP')
    
    # Convert to circular CORRECTED_DATA -> CORRECTED_DATA
    logger.info('Converting to circular...')
    MSs.run('mslin2circ.py -i $pathMS:CORRECTED_DATA -o $pathMS:CORRECTED_DATA', log='$nameMS_circ2lin.log', commandType='python', maxThreads=5)
    
    # Solve cal_SB.MS:CORRECTED_DATA (only solve)
    logger.info('Solving FR...')
    MSs.run('DPPP ' + parset_dir + '/DPPP-soldd.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA sol.h5parm=$pathMS/fr.h5 sol.mode=diagonal \
            sol.uvlambdarange='+str(nouseblrange), log='$nameMS_solFR.log', commandType="DPPP")
    
    lib_util.run_losoto(s, 'fr-c'+str(c), [ms+'/fr.h5' for ms in MSs.getListStr()], \
            [parset_dir + '/losoto-fr.parset'])

   #####################################################
   # 3: find BANDPASS/IONO

    # Beam correction DATA -> CORRECTED_DATA
    logger.info('Polalign correction...')
    MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS msin.datacolumn=DATA cor.parmdb=cal-pa-c'+str(c)+'.h5 cor.correction=polalign', \
            log='$nameMS_corPA3.log', commandType="DPPP")

    # Beam correction (and update weight in case of imaging) CORRECTED_DATA -> CORRECTED_DATA
    logger.info('Beam correction...')
    if c == 0 and lofar_system == 'lba':
        MSs.run('DPPP '+parset_dir+'/DPPP-beam.parset msin=$pathMS corrbeam.updateweights=True', log='$nameMS_corBEAM3.log', commandType='DPPP')
    else:
        MSs.run('DPPP '+parset_dir+'/DPPP-beam.parset msin=$pathMS corrbeam.updateweights=False', log='$nameMS_corBEAM3.log', commandType='DPPP')
 
    # Correct FR CORRECTED_DATA -> CORRECTED_DATA
    logger.info('Faraday rotation correction...')
    MSs.run('DPPP ' + parset_dir + '/DPPP-cor.parset msin=$pathMS cor.parmdb=cal-fr-c'+str(c)+'.h5 cor.correction=rotationmeasure000', \
            log='$nameMS_corFR3.log', commandType="DPPP")

    # Solve cal_SB.MS:CORRECTED_DATA (only solve)
    logger.info('Solving IONO...')
    MSs.run('DPPP ' + parset_dir + '/DPPP-soldd.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA sol.h5parm=$pathMS/iono.h5 sol.mode=diagonal \
                                    sol.uvlambdarange='+str(nouseblrange), log='$nameMS_solIONO3.log', commandType="DPPP")

    lib_util.run_losoto(s, 'iono-c'+str(c), [ms+'/iono.h5' for ms in MSs.getListStr()], \
                        [parset_dir+'/losoto-flag.parset',parset_dir+'/losoto-plot-amp.parset',parset_dir+'/losoto-plot-ph.parset'])

    # Correct all CORRECTED_DATA -> CORRECTED_DATA
    logger.info('IONO correction...')
    MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS cor.updateweights=False cor.parmdb=cal-iono-c'+str(c)+'.h5 cor.correction=phase000', \
                                log='$nameMS_corIONO3.log', commandType='DPPP')

    # Solve MS:CORRECTED_DATA (only solve)
    logger.info('Solving BP...')
    MSs.run('DPPP ' + parset_dir + '/DPPP-soldd.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA sol.h5parm=$pathMS/amp.h5 sol.mode=diagonal sol.flagunconverged=False \
            sol.uvlambdarange='+str(nouseblrange)+' sol.nchan=2 sol.solint=10', log='$nameMS_solAMP3.log', commandType="DPPP")
    
    lib_util.run_losoto(s, 'amp-c'+str(c), [ms+'/amp.h5' for ms in MSs.getListStr()], \
            [parset_dir+'/losoto-plot-amp.parset'])

    # Correct BP CORRECTED_DATA -> CORRECTED_DATA
    logger.info('BP correction...')
    if c == 0 and lofar_system == 'lba':
        MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS cor.updateweights=True cor.parmdb=cal-iono-c'+str(c)+'.h5 cor.correction=amplitude000', \
                log='$nameMS_corAMP3.log', commandType='DPPP')
    else:
        MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS cor.updateweights=False cor.parmdb=cal-iono-c'+str(c)+'.h5 cor.correction=amplitude000', \
               log='$nameMS_corAMP3.log', commandType='DPPP')

#    # Solve MS:DATA (only solve)
#    logger.info('Solving all...')
#    MSs.run('DPPP ' + parset_dir + '/DPPP-soldd.parset msin=$pathMS msin.datacolumn=SMOOTHED_DATA sol.h5parm=$pathMS/iono.h5 sol.mode=fulljones', \
#            log='$nameMS_solFJ-c'+str(c)+'.log', commandType="DPPP")
#    
#    lib_util.run_losoto(s, 'iono-c'+str(c), [ms+'/iono.h5' for ms in MSs.getListStr()], \
#            [parset_dir+'/losoto-flag.parset',parset_dir+'/losoto-plot-amp.parset',parset_dir+'/losoto-plot-ph.parset'])
#    
#    # Correct all DATA -> CORRECTED_DATA
#    logger.info('IONO correction...')
#    if c == 0 and lofar_system == 'lba':
#        MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS msin.datacolumn=DATA cor.updateweights=True cor.parmdb=cal-iono-c'+str(c)+'.h5 cor.correction=fulljones cor.soltab=[amplitude000,phase000]', \
#                log='$nameMS_cor-c'+str(c)+'.log', commandType='DPPP')
#    else:
#        MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS msin.datacolumn=DATA cor.updateweights=False cor.parmdb=cal-iono-c'+str(c)+'.h5 cor.correction=fulljones cor.soltab=[amplitude000,phase000]', \
#                log='$nameMS_cor-c'+str(c)+'.log', commandType='DPPP')

       
    logger.info('Cleaning (cycle %02i)...' % c)
    imagename = 'img/img-c%02i' % c
    #use_weights_as_taper='',\
    if patch == 'CygA':
        lib_util.run_wsclean(s, 'wsclean-c'+str(c)+'.log', MSs.getStrWsclean(), name=imagename, save_source_list='', size=1000, scale='1.5arcsec', \
                weight='briggs -1.5', niter=50000, no_update_model_required='', nmiter=50, mgain=0.5, \
                multiscale='', multiscale_scale_bias=0.6, multiscale_scales='0,5,10,20,40', \
                fits_mask='/home/fdg/scripts/LiLF/parsets/LOFAR_ateam/masks/CygA.fits', \
                baseline_averaging=5, auto_threshold=1, \
                join_channels='', deconvolution_channels=5, fit_spectral_pol=2, channels_out=61)

    elif patch == 'CasA':
        #RMS per scale: {0: 16.87 mJy, 13: 13.81 mJy, 27: 11.81 mJy, 53: 9.35 mJy, 106: 6.34 mJy, 212: 2.96 mJy, 425: 1.4 mJy}
        lib_util.run_wsclean(s, 'wsclean-c'+str(c)+'.log', MSs.getStrWsclean(), name=imagename, save_source_list='', size=1300, scale='2arcsec', \
                weight='briggs -1.2', niter=75000, no_update_model_required='', nmiter=50, mgain=0.5, \
                multiscale='', multiscale_scale_bias=0.7, multiscale_scales='0,5,10,20,40,80', \
                fits_mask='/home/fdg/scripts/LiLF/parsets/LOFAR_ateam/masks/CasA.fits', \
                baseline_averaging=5, auto_threshold=1, \
                join_channels='', deconvolution_channels=5, fit_spectral_pol=2, channels_out=61)

    elif patch == 'TauA':
        lib_util.run_wsclean(s, 'wscleanA-c'+str(c)+'.log', MSs.getStrWsclean(), name=imagename, save_source_list='', size=1500, scale='1arcsec', \
                weight='briggs -1.0', niter=30, update_model_required='', mgain=0.3, \
                fits_mask='/home/fdg/scripts/LiLF/parsets/LOFAR_ateam/masks/pulsar.fits', \
                join_channels='', deconvolution_channels=5, fit_spectral_pol=2, channels_out=61) # use cont=True
        lib_util.run_wsclean(s, 'wscleanB-c'+str(c)+'.log', MSs.getStrWsclean(), cont=True, name=imagename, save_source_list='', size=1500, scale='1arcsec', \
                weight='briggs -1.0', niter=100000, no_update_model_required='', nmiter=50, mgain=0.5, \
                multiscale='', multiscale_scale_bias=0.7, multiscale_scales='0,5,10,20,40,80', \
                fits_mask='/home/fdg/scripts/LiLF/parsets/LOFAR_ateam/masks/TauA.fits', \
                auto_threshold=1, \
                join_channels='', deconvolution_channels=5, fit_spectral_pol=2, channels_out=61)
        for modelfile in glob.glob(imagename+'*model*'):
            rev_reg(modelfile,'/home/fdg/scripts/LiLF/parsets/LOFAR_ateam/masks/tauhole.reg')

    elif patch == 'VirA' and lofar_system == 'lba':
        #lib_util.run_wsclean(s, 'wscleanA-c'+str(c)+'.log', MSs.getStrWsclean(), name=imagename, size=1500, scale='2arcsec', \
        #        weight='briggs -1.5', niter=500, update_model_required='', mgain=0.2, \
        #        multiscale='', multiscale_scale_bias=0.7, multiscale_scales='0,5,10', \
        #        join_channels='', deconvolution_channels=5, fit_spectral_pol=2, channels_out=61) # use cont=True
        lib_util.run_wsclean(s, 'wsclean-c'+str(c)+'.log', MSs.getStrWsclean(), name=imagename, save_source_list='', size=1500, scale='2arcsec', \
                weight='briggs -1.0', niter=50000, no_update_model_required='', nmiter=50, mgain=0.4, \
                multiscale='', multiscale_scale_bias=0.7, multiscale_scales='0,5,10,20,40,80,160', \
                fits_mask='/home/fdg/scripts/LiLF/parsets/LOFAR_ateam/masks/VirAlba.fits', \
                auto_threshold=5, \
                join_channels='', deconvolution_channels=5, fit_spectral_pol=2, channels_out=61)
        for modelfile in glob.glob(imagename+'*model*'):
            rev_reg(modelfile,'/home/fdg/scripts/LiLF/parsets/LOFAR_ateam/masks/virgohole.reg')

        # to do the minihalo
        lib_util.run_wsclean(s, 'wscleanLR-c'+str(c)+'.log', MSs.getStrWsclean(), name=imagename+'LR', size=1000, scale='15arcsec', \
                weight='briggs 0.', taper_gaussian='50arcsec', niter=30000, no_update_model_required='', mgain=0.85, \
                baseline_averaging=5, auto_threshold=1, \
                join_channels='', deconvolution_channels=5, fit_spectral_pol=2, channels_out=61)

    elif patch == 'VirA' and lofar_system == 'hba':
        lib_util.run_wsclean(s, 'wscleanA-c'+str(c)+'.log', MSs.getStrWsclean(), name=imagename, size=2500, scale='1arcsec', \
                weight='briggs -0.7', niter=3000, update_model_required='', mgain=0.3, \
                fits_mask='/home/fdg/scripts/LiLF/parsets/LOFAR_ateam/masks/VirAphba.fits', \
                join_channels='', deconvolution_channels=5, fit_spectral_pol=2, channels_out=61) # use cont=True
        lib_util.run_wsclean(s, 'wsclean-c'+str(c)+'.log', MSs.getStrWsclean(), cont=True, name=imagename, size=2500, scale='1arcsec', \
                weight='briggs -0.7', niter=50000, no_update_model_required='', nmiter=50, mgain=0.3, \
                multiscale='', multiscale_scale_bias=0.7, multiscale_scales='0,5,10,20,40,80,160', \
                fits_mask='/home/fdg/scripts/LiLF/parsets/LOFAR_ateam/masks/VirAhba.fits', \
                auto_threshold=1, \
                join_channels='', deconvolution_channels=5, fit_spectral_pol=2, channels_out=61)
        for modelfile in glob.glob(imagename+'*model*'):
            rev_reg(modelfile,'/home/fdg/scripts/LiLF/parsets/LOFAR_ateam/masks/virgohole.reg')

    logger.info('Predict (wsclean: %s)...' % imagename)
    s.add('wsclean -predict -name '+imagename+' -j '+str(s.max_processors)+' -channels-out 61 '+MSs.getStrWsclean(), \
          log='wscleanPRE-c'+str(c)+'.log', commandType='wsclean', processors='max')
    s.run(check=True)

    #logger.info('Reweight...')
    #MSs.run('reweight.py -v -p -d CORRECTED_DATA -m residual $pathMS', log='$nameMS_weight.log', commandType='general')
    #os.system('mkdir weights-c'+str(c)+'; mv *png weights-c'+str(c))
        
    # every 5 cycles: sub model and rescale model
    if c%5 == 0 and c != 0:

        logger.info('Sub model...')
        MSs.run('taql "update $pathMS set CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA"', log='$nameMS_taql1.log', commandType='general')

        logger.info('Cleaning wide (cycle %i)...' % c)
        imagename = 'img/imgsub-c'+str(c)
        lib_util.run_wsclean(s, 'wscleanSUB-c'+str(c)+'.log', MSs.getStrWsclean(), name=imagename, size=1000, scale='15arcsec', \
                weight='briggs 0.4', taper_gaussian='100arcsec', niter=10000, no_update_model_required='', mgain=0.85, \
                baseline_averaging=5, deconvolution_channels=4, \
                auto_threshold=1, join_channels='', fit_spectral_pol=2, channels_out=16)
 
        #logger.info('Predict wide (wsclean)...')
        #s.add('wsclean -predict -name '+imagename+' -j '+str(s.max_processors)+' -channelsout 32 '+MSs.getStrWsclean(), \
        #      log='wscleanPRE-c'+str(c)+'.log', commandType='wsclean', processors='max')
        #s.run(check = True)

        #logger.info('Sub low-res model...')
        #MSs.run('taql "update $pathMS set CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA"', log='$nameMS_taql2.log', commandType='general')

logger.info("Done.")
