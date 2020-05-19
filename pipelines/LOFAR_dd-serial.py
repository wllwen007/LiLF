#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Pipeline for direction dependent calibration

import sys, os, glob, re, pickle
import numpy as np
import pyrap.tables as pt
import lsmtool

#######################################################
from LiLF import lib_ms, lib_img, lib_util, lib_log, lib_dd, lib_h5
logger_obj = lib_log.Logger('pipeline-dd-serial.logger')
logger = lib_log.logger
s = lib_util.Scheduler(log_dir = logger_obj.log_dir, dry = False)
w = lib_util.Walker('pipeline-dd-serial.walker')

# parse parset
parset = lib_util.getParset()
parset_dir = parset.get('LOFAR_dd-serial','parset_dir')
userReg = parset.get('model','userReg')
maxIter = parset.getint('LOFAR_dd-serial','maxIter')
min_cal_flux = parset.getfloat('LOFAR_dd-serial','minCalFlux60')
removeExtendedCutoff = parset.getfloat('LOFAR_dd-serial','removeExtendedCutoff')

def clean(p, MSs, res='normal', size=[1,1], empty=False):
    """
    p = patch name
    mss = list of mss to clean
    size = in deg of the image
    """
    # set pixscale and imsize
    pixscale = MSs.getListObj()[0].getResolution() 
    if res == 'normal':
        pixscale = float('%.1f'%(pixscale/2.5))
    elif res == 'high':
        pixscale = float('%.1f'%(pixscale/3.5))
    elif res == 'low':
        pass # no change

    imsize = [int(size[0]*1.5/(pixscale/3600.)), int(size[1]*1.5/(pixscale/3600.))] # add 50%
    imsize[0] += imsize[0] % 2
    imsize[1] += imsize[1] % 2
    if imsize[0] < 256: imsize[0] = 256
    if imsize[1] < 256: imsize[1] = 256

    logger.debug('Image size: '+str(imsize)+' - Pixel scale: '+str(pixscale))

    if res == 'normal':
        weight = 'briggs -0.3'
        maxuv_l = None
    elif res == 'high':
        weight = 'briggs -0.6'
        maxuv_l = None
    elif res == 'low':
        weight = 'briggs 0'
        maxuv_l = 3500
    else:
        logger.error('Wrong "res": %s.' % str(res))
        sys.exit()

    if empty:

        logger.info('Cleaning empty ('+str(p)+')...')
        imagename = 'img/empty-'+str(p)
        lib_util.run_wsclean(s, 'wscleanE-'+str(p)+'.log', MSs.getStrWsclean(), name=imagename, data_column='SUBTRACTED_DATA', \
                size=imsize, scale=str(pixscale)+'arcsec', \
                weight=weight, niter=0, no_update_model_required='', minuv_l=30, mgain=0, \
                baseline_averaging=5)
 
    else:

        # clean 1
        logger.info('Cleaning ('+str(p)+')...')
        imagename = 'img/ddcal-'+str(p)
        lib_util.run_wsclean(s, 'wscleanA-'+str(p)+'.log', MSs.getStrWsclean(), name=imagename, \
                size=imsize, scale=str(pixscale)+'arcsec', \
                weight=weight, niter=10000, no_update_model_required='', minuv_l=30, maxuv_l=maxuv_l, mgain=0.85, \
                baseline_averaging=3, parallel_deconvolution=512, auto_threshold=5, \
                join_channels='', fit_spectral_pol=3, channels_out=9, deconvolution_channels=3)
    
        # make mask
        im = lib_img.Image(imagename+'-MFS-image.fits', userReg=userReg)
        try:
            im.makeMask(threshisl = 7, rmsbox=(70,5))
        except:
            logger.warning('Fail to create mask for %s.' % imagename+'-MFS-image.fits')
            return
    
        # clean 2
        logger.info('Cleaning w/ mask ('+str(p)+')...')
        imagename = 'img/ddcalM-'+str(p)
        lib_util.run_wsclean(s, 'wscleanB-'+str(p)+'.log', MSs.getStrWsclean(), name=imagename, do_predict=True, \
                size=imsize, save_source_list='', scale=str(pixscale)+'arcsec', \
                weight=weight, niter=100000, no_update_model_required='', minuv_l=30, maxuv_l=maxuv_l, mgain=0.85, \
                multiscale='', multiscale_scale_bias=0.65, multiscale_scales='0,10,20,40,80', 
                baseline_averaging=3, parallel_deconvolution=512, local_rms='', auto_threshold=0.75, auto_mask=1.5, fits_mask=im.maskname, \
                join_channels='', fit_spectral_pol=3, channels_out=9, deconvolution_channels=3)

        os.system('cat logs/wscleanA-'+str(p)+'.log logs/wscleanB-'+str(p)+'.log | grep "background noise"')


#############################################################
if w.todo('cleaning'):
    logger.info('Cleaning...')
    lib_util.check_rm('ddcal')
    os.makedirs('ddcal/masks')
    os.makedirs('ddcal/plots')
    os.makedirs('ddcal/images')
    os.makedirs('ddcal/solutions')
    os.makedirs('ddcal/skymodels')
    os.makedirs('ddcal/aterm')

    w.done('cleaning')
### DONE

MSs = lib_ms.AllMSs( glob.glob('mss/TC*[0-9].MS'), s )

# make beam
fwhm = MSs.getListObj()[0].getFWHM(freq='mid')
freq_min = np.min(MSs.getListObj()[0].getFreqs())
freq_mid = np.mean(MSs.getListObj()[0].getFreqs())
min_cal_flux *= (freq_min/60.e6)**(-0.8) # rescale min flux at 60 MHz to min freq
phase_center = MSs.getListObj()[0].getPhaseCentre()
timeint = MSs.getListObj()[0].getTimeInt()
logger.info('Add columns...')

MSs.run('addcol2ms.py -m $pathMS -c SUBTRACTED_DATA -i DATA', log='$nameMS_addcol.log', commandType='python')
MSs.run('addcol2ms.py -m $pathMS -c FLAG_BKP -i FLAG', log='$nameMS_addcol.log', commandType='python')

##############################################################
# setup initial model
MSs.getListObj()[0].makeBeamReg('ddcal/beam.reg', freq='mid')
beamReg = 'ddcal/beam.reg'
mosaic_image = lib_img.Image(sorted(glob.glob('self/images/wideM-[0-9]-MFS-image.fits'))[-1], userReg = userReg)

for cmaj in range(maxIter):
    logger.info('Starting major cycle: %i' % cmaj)
    
    if w.todo('c%02i-delimg' % cmaj):
        lib_util.check_rm('img')
        os.makedirs('img')
        w.done('c%02i-delimg' % cmaj)
    ### DONE

    skymodel_cl = 'ddcal/skymodels/skymodel%02i_cluster.txt' % cmaj
    skymodel_cl_skydb = skymodel_cl.replace('.txt','.skydb')

    picklefile = 'ddcal/directions-c%02i.pickle' % cmaj

    if not os.path.exists(picklefile):
        directions = []

        if not os.path.exists('ddcal/masks/regions-c%02i' % cmaj): os.makedirs('ddcal/masks/regions-c%02i' % cmaj)
        if not os.path.exists('ddcal/images/c%02i' % cmaj): os.makedirs('ddcal/images/c%02i' % cmaj)
    
        ### group into patches corresponding to the mask islands
        mask_cl = mosaic_image.imagename.replace('image.fits', 'mask-cl.fits')
        # this mask is with no user region, done to isolate only bight compact sources
        if not os.path.exists(mosaic_image.skymodel_cut): 
            mosaic_image.beamReg = 'ddcal/beam.reg'
            mosaic_image.makeMask(threshisl=4, atrous_do=False, remove_extended_cutoff=removeExtendedCutoff, only_beam=False, maskname=mask_cl)
            mosaic_image.selectCC(checkBeam=False, maskname=mask_cl)
        
        lsm = lsmtool.load(mosaic_image.skymodel_cut)
        lsm.group(mask_cl, root='Isl')
        # This regroup nearby sources
        x = lsm.getColValues('RA',aggregate='wmean')
        y = lsm.getColValues('Dec',aggregate='wmean')
        flux = lsm.getColValues('I',aggregate='sum')
        grouper = lib_dd.Grouper(list(zip(x,y)), flux, look_distance=0.2, kernel_size=0.1, grouping_distance=0.03)
        grouper.run()
        clusters = grouper.grouping()
        grouper.plot()
        os.system('mv grouping*png ddcal/plots/')
        patchNames = lsm.getPatchNames()
    
        logger.info('Merging nearby sources...')
        for cluster in clusters:
            patches = patchNames[cluster]
            #print('Merging:', patches)
            if len(patches) > 1:
                lsm.merge(patches.tolist())
    
        lsm.setPatchPositions(method='mid')
        for name, flux, size, ra, dec in \
                zip( lsm.getPatchNames(), lsm.getColValues('I', aggregate='sum'), lsm.getPatchSizes(units='deg'), \
                     lsm.getPatchPositions(asArray=True)[0], lsm.getPatchPositions(asArray=True)[1] ):
            # keep track of the spidx of sources
            idx = lsm.getRowIndex(name)
            fluxes = lsm.getColValues('I')[idx]
            spidx_coeffs = lsm.getColValues('SpectralIndex')[idx]
            ref_freq = lsm.getColValues('ReferenceFrequency')[idx]

            direction = lib_dd.Direction(name)
            direction.set_position( [ra, dec] )
            direction.set_flux(fluxes, spidx_coeffs, ref_freq )
            direction.set_size([size*1.2,size*1.2]) # size increased by 20%
            directions.append(direction)
            #print('%s: %f Jy (flux at min freq: %f Jy)' % (name,flux,direction.get_flux(freq_min)))

        # order directions from the fluxiest
        directions = [x for _,x in sorted(zip([d.get_flux(freq_min) for d in directions],directions))][::-1] # reorder with flux
        # TEST for NEST
        #d=directions[0]
        #directions.insert(12,d)
        #directions.pop(0)

        for d in directions:
            if d.get_flux(freq_min) < min_cal_flux: break
            logger.info( '%s: min: %.2f Jy; mid: %.2f Jy' % (d.name, d.get_flux(freq_min), d.get_flux(freq_mid)) )

        # write file
        lsm.write(skymodel_cl, format='makesourcedb', clobber=True)
        lsm.setColValues('name', [x.split('_')[-1] for x in lsm.getColValues('patch')]) # just for the region - this makes this lsm useless
        lsm.write('ddcal/masks/regions-c%02i/cluster.reg' % cmaj, format='ds9', clobber=True)
        del lsm
    
        # convert to blob
        lib_util.check_rm(skymodel_cl_skydb)
        s.add('makesourcedb outtype="blob" format="<" in="%s" out="%s"' % (skymodel_cl, skymodel_cl_skydb), log='makesourcedb_cl.log', commandType='general' )
        s.run(check=True)
        
        pickle.dump( directions, open( picklefile, "wb" ) )
    else:
        directions = pickle.load( open( picklefile, "rb" ) )

    if w.todo('c%02i-fullsub' % cmaj):

        if os.path.exists('mss-subtract'):
            logger.warning('Reuse old mss-subtract.')
            os.system('rm -r mss')
            os.system('cp -r mss-subtract mss')
        else:
            # subtract everything - ms:CORRECTED_DATA -> ms:SUBTRACTED_DATA
            logger.info('Subtract everything from CORRECTED_DATA and put the result in SUBTRACTED_DATA...')
            MSs.run('DPPP '+parset_dir+'/DPPP-predict.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA msout.datacolumn=SUBTRACTED_DATA \
                    pre.operation=subtract pre.sourcedb='+skymodel_cl_skydb, \
                    log='$nameMS_sub-c'+str(cmaj)+'.log', commandType='DPPP')
    
            os.system('cp -r mss mss-subtract')

        w.done('c%02i-fullsub' % cmaj)
    ### DONE

    ### TTESTTESTTEST: empty image
    if not os.path.exists('img/empty-init-c'+str(cmaj)+'-image.fits'):
        clean('init-c'+str(cmaj), MSs, size=(fwhm*1.5,fwhm*1.5), res='normal', empty=True)
    ###
 
    for dnum, d in enumerate(directions):
        # arrive down to calibrators of flux = 1 Jy
        if d.get_flux(freq_min) < min_cal_flux: break

        logger.info('c%02i - Working on direction: %s (%f Jy - %f deg)' % (cmaj, d.name, d.get_flux(freq_min), d.size[0]))
        logstring = 'c%02i-%s' % (cmaj,d.name)

        # Prepare the skymodel:
        # Load full skymodel and extract sources in the square around the calibrator of the given size
        if d.size[0] > 0.5 or d.size[1] > 0.5: logger.warning('Patch size large: [%f - %f]' % (d.size[0], d.size[1]))
    
        os.system('cp %s ddcal/skymodels/%s.skymodel' % (mosaic_image.skymodel_cut, d.name) )
        d.set_skymodel('ddcal/skymodels/%s.skymodel' % d.name, doskydb=True, restrict=True)

        if w.todo('%s-shift' % logstring):
            logger.info('Phase shift and avg...')
            
            lib_util.check_rm('mss-dir')
            os.makedirs('mss-dir')

            # Shift - ms:SUBTRACTED_DATA -> ms:DATA
            if d.get_flux(freq_mid) > 4: avgtimeint = int(16/timeint)
            else: avgtimeint = int(32/timeint)
            MSs.run('DPPP '+parset_dir+'/DPPP-shiftavg.parset msin=$pathMS msout=mss-dir/$nameMS.MS msin.datacolumn=SUBTRACTED_DATA msout.datacolumn=DATA \
                    avg.timestep='+str(avgtimeint)+' avg.freqstep=8 shift.phasecenter=['+str(d.position[0])+'deg,'+str(d.position[1])+'deg\]', \
                    log='$nameMS_shift-'+logstring+'.log', commandType='DPPP')

            w.done('%s-shift' % logstring)
        ### DONE

        MSs_dir = lib_ms.AllMSs( glob.glob('mss-dir/*MS'), s )

        if w.todo('%s-predict' % logstring):

            logger.info('Flag on mindata...')
            MSs_dir.run( 'flagonmindata.py -f 0.5 $pathMS', log='$nameMS_flagonmindata.log', commandType='python')

            # Predict - ms:MODEL_DATA
            logger.info('Add ddcal model to MODEL_DATA...')
            MSs_dir.run('DPPP '+parset_dir+'/DPPP-predict.parset msin=$pathMS msin.datacolumn=DATA msout.datacolumn=MODEL_DATA \
                    pre.sourcedb='+d.get_skydb(-1), \
                    log='$nameMS_pre-'+logstring+'.log', commandType='DPPP')

            # Add back the model previously subtracted for this dd-cal
            logger.info('Set DATA = DATA + MODEL_DATA...')
            MSs_dir.run('taql "update $pathMS set DATA = DATA + MODEL_DATA"', \
                    log='$nameMS_taql-'+logstring+'.log', commandType='general')

            w.done('%s-predict' % logstring)
        ### DONE

        if w.todo('%s-preimage' % logstring):

            logger.info('Pre-imaging...')
            clean('%s-pre' % logstring, MSs_dir, res='normal', size=d.size)

            w.done('%s-preimage' % logstring)
        ### DONE
        
        # get initial noise and set iterators for timeint solutions
        image = lib_img.Image('img/ddcalM-%s-pre-MFS-image.fits' % logstring)
        rms_noise_pre = image.getNoise()
        rms_noise_init = rms_noise_pre
        doamp = False
        if d.get_flux(freq_mid) > 4: iter_ph_solint = lib_util.Sol_iterator([4,1])
        else: iter_ph_solint = lib_util.Sol_iterator([4,2])
        iter_amp_solint = lib_util.Sol_iterator([30,20,10]) # usually there are 3600/2/6=300 timesteps, try to use multiple numbers
        iter_amp2_solint = lib_util.Sol_iterator([60,30,20]) # usually there are 3600/2/6=300 timesteps, try to use multiple numbers
        logger.info('RMS noise (init): %f' % (rms_noise_pre))
        # set initial skymodel
        d.set_skymodel('img/ddcalM-%s-pre-sources.txt' % logstring, doskydb=True)

        for cdd in range(10):

            logger.info('c%02i - %s: Starting dd cycle: %02i' % (cmaj, d.name, cdd))
            logstringcal = logstring+'-cdd%02i' % cdd

            #########################################################
            # Predict - ms:MODEL_DATA
            if w.todo('%s-predict' % logstringcal):

                logger.info('Add ddcal model to MODEL_DATA...')
                MSs_dir.run('DPPP '+parset_dir+'/DPPP-predict.parset msin=$pathMS msin.datacolumn=DATA msout.datacolumn=MODEL_DATA \
                    pre.sourcedb='+d.get_skydb(-1), \
                    log='$nameMS_pre-'+logstringcal+'.log', commandType='DPPP')

                w.done('%s-predict' % logstringcal)
            ### DONE


            ################################################################
            # Calibrate
            solint_ph = next(iter_ph_solint)
            d.set_h5parm('ph', 'ddcal/solutions/cal-ph-%s.h5' % logstringcal )
            if doamp:
                solint_amp = next(iter_amp_solint)
                solint_amp2 = next(iter_amp2_solint)
                d.set_h5parm('amp1', 'ddcal/solutions/cal-amp1-%s.h5' % logstringcal )
                d.set_h5parm('amp2', 'ddcal/solutions/cal-amp2-%s.h5' % logstringcal )
   
            if w.todo('%s-calibrate' % logstringcal):

                logger.info('BL-based smoothing...')
                # Smoothing - ms:DATA -> ms:SMOOTHED_DATA
                MSs_dir.run('BLsmooth.py -r -i DATA -o SMOOTHED_DATA $pathMS', \
                    log='$nameMS_smooth-'+logstringcal+'.log', commandType='python')    
 
                # Calibration - ms:SMOOTHED_DATA
                logger.info('Gain phase calibration...')
                MSs_dir.run('DPPP '+parset_dir+'/DPPP-solG.parset msin=$pathMS msin.datacolumn=SMOOTHED_DATA \
                    sol.h5parm=$pathMS/cal-ph.h5 sol.solint='+str(solint_ph)+' sol.mode=tecandphase \
                    sol.antennaconstraint=[[CS001LBA,CS002LBA,CS003LBA,CS004LBA,CS005LBA,CS006LBA,CS007LBA,CS011LBA,CS013LBA,CS017LBA,CS021LBA,CS024LBA,CS026LBA,CS028LBA,CS030LBA,CS031LBA,CS032LBA,CS101LBA,CS103LBA,CS201LBA,CS301LBA,CS302LBA,CS401LBA,CS501LBA]]', \
                    log='$nameMS_solGph-'+logstringcal+'.log', commandType='DPPP')
                lib_util.run_losoto(s, 'ph', [ms+'/cal-ph.h5' for ms in MSs_dir.getListStr()], \
                    [parset_dir+'/losoto-plot-tec.parset'], plots_dir='ddcal/plots/plots-%s' % logstringcal)
                os.system('mv cal-ph.h5 ddcal/solutions/cal-ph-%s.h5' % logstringcal)

                # correct ph - ms:DATA -> ms:CORRECTED_DATA
                logger.info('Correct ph...')
                MSs_dir.run('DPPP '+parset_dir+'/DPPP-correct.parset msin=$pathMS msin.datacolumn=DATA msout.datacolumn=CORRECTED_DATA \
                             cor.parmdb=ddcal/solutions/cal-ph-'+logstringcal+'.h5 cor.correction=tec000', \
                             log='$nameMS_correct-'+logstringcal+'.log', commandType='DPPP')
                MSs_dir.run('DPPP '+parset_dir+'/DPPP-correct.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA msout.datacolumn=CORRECTED_DATA \
                             cor.parmdb=ddcal/solutions/cal-ph-'+logstringcal+'.h5 cor.correction=phase000', \
                             log='$nameMS_correct-'+logstringcal+'.log', commandType='DPPP')

                if doamp:
                    logger.info('BL-based smoothing...')
                    # Smoothing - ms:CORRECTED_DATA -> ms:SMOOTHED_DATA
                    MSs_dir.run('BLsmooth.py -r -i CORRECTED_DATA -o SMOOTHED_DATA $pathMS', \
                        log='$nameMS_smooth-'+logstringcal+'.log', commandType='python')    
                    
                    logger.info('Gain amp calibration 1...')
                    # Calibration - ms:SMOOTHED_DATA
                    MSs_dir.run('DPPP '+parset_dir+'/DPPP-solG.parset msin=$pathMS msin.datacolumn=SMOOTHED_DATA sol.mode=diagonal \
                        sol.antennaconstraint=[[CS001LBA,CS002LBA,CS003LBA,CS004LBA,CS005LBA,CS006LBA,CS007LBA,CS011LBA,CS013LBA,CS017LBA,CS021LBA,CS024LBA,CS026LBA,CS028LBA,CS030LBA,CS031LBA,CS032LBA,CS101LBA,CS103LBA,CS201LBA,CS301LBA,CS302LBA,CS401LBA,CS501LBA,RS106LBA,RS205LBA,RS208LBA,RS210LBA,RS305LBA,RS306LBA,RS307LBA,RS310LBA,RS406LBA,RS407LBA,RS409LBA,RS503LBA,RS508LBA,RS509LBA]] \
                        sol.h5parm=$pathMS/cal-amp1.h5 sol.uvmmin=300 sol.smoothnessconstraint=1e6 sol.solint='+str(solint_amp), \
                        log='$nameMS_solGamp1-'+logstringcal+'.log', commandType='DPPP')
                    lib_util.run_losoto(s, 'amp1', [ms+'/cal-amp1.h5' for ms in MSs_dir.getListStr()], \
                        [parset_dir+'/losoto-amp.parset', parset_dir+'/losoto-clip.parset', parset_dir+'/losoto-plot2.parset'], plots_dir='ddcal/plots/plots-%s' % logstringcal)
                    os.system('mv cal-amp1.h5 ddcal/solutions/cal-amp1-%s.h5' % logstringcal)

                    logger.info('Correct amp 1...')
                    # correct amp - ms:CORRECTED_DATA -> ms:CORRECTED_DATA
                    MSs_dir.run('DPPP '+parset_dir+'/DPPP-correct.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA msout.datacolumn=CORRECTED_DATA \
                        cor.parmdb=ddcal/solutions/cal-amp1-'+logstringcal+'.h5 cor.correction=amplitude000', \
                        log='$nameMS_correct-'+logstringcal+'.log', commandType='DPPP') 

                    logger.info('BL-based smoothing...')
                    # Smoothing - ms:CORRECTED_DATA -> ms:SMOOTHED_DATA
                    MSs_dir.run('BLsmooth.py -r -i CORRECTED_DATA -o SMOOTHED_DATA $pathMS', \
                        log='$nameMS_smooth-'+logstringcal+'.log', commandType='python')    

                    logger.info('Gain amp calibration 2...')
                    # Calibration - ms:SMOOTHED_DATA
                    MSs_dir.run('DPPP '+parset_dir+'/DPPP-solG.parset msin=$pathMS msin.datacolumn=SMOOTHED_DATA sol.mode=diagonal \
                        sol.h5parm=$pathMS/cal-amp2.h5 sol.uvmmin=300 sol.smoothnessconstraint=10e6 sol.solint='+str(solint_amp2), \
                        log='$nameMS_solGamp2-'+logstringcal+'.log', commandType='DPPP')
                    lib_util.run_losoto(s, 'amp2', [ms+'/cal-amp2.h5' for ms in MSs_dir.getListStr()], \
                        [parset_dir+'/losoto-amp.parset', parset_dir+'/losoto-clip.parset', parset_dir+'/losoto-plot3.parset'], plots_dir='ddcal/plots/plots-%s' % logstringcal)
                    lib_util.check_rm('ddcal/solutions/cal-amp2-%s.h5' % logstringcal)
                    os.system('mv cal-amp2.h5 ddcal/solutions/cal-amp2-%s.h5' % logstringcal)

                    logger.info('Correct amp 2...')
                    # correct amp2 - ms:CORRECTED_DATA -> ms:CORRECTED_DATA
                    MSs_dir.run('DPPP '+parset_dir+'/DPPP-correct.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA msout.datacolumn=CORRECTED_DATA \
                        cor.parmdb=ddcal/solutions/cal-amp2-'+logstringcal+'.h5 cor.correction=amplitude000', \
                        log='$nameMS_correct-'+logstringcal+'.log', commandType='DPPP') 

                w.done('%s-calibrate' % logstringcal)
            ### DONE


            ###########################################################################
            # Imaging
            if w.todo('%s-image' % logstringcal):

                logger.info('%s (cdd: %02i): imaging...' % (d.name, cdd))
                clean('%s' % logstringcal, MSs_dir, res='normal', size=d.size)

                w.done('%s-image' % logstringcal)
            ### DONE

            # update skymodel
            d.set_skymodel( 'img/ddcalM-%s-sources.txt' % logstringcal, doskydb=True, restrict=True )
        
            # get noise, if larger than prev cycle: break
            image = lib_img.Image('img/ddcalM-%s-MFS-image.fits' % logstringcal)
            if not os.path.exists(image.imagename): break # something went wrong during last imaging
            rms_noise = image.getNoise()
            logger.info('RMS noise (cdd:%02i): %f' % (cdd,rms_noise))
            if rms_noise > rms_noise_pre and cdd >= 2 and doamp: break
            if rms_noise > 0.99*rms_noise_pre and cdd >= 1: doamp = True
            rms_noise_pre = rms_noise


        # End calibration cycle
        ##################################

        # if divergency, don't subtract
        if rms_noise_pre > 2*rms_noise_init:
            logger.warning('%s: noise did not decresed (%f -> %f), do not subtract source.' % (d.name, rms_noise_init, rms_noise_pre))
            d.clean()
            continue
        else:
            d.converged = True
        
        # remove the DD-cal from original dataset using new solutions
        if w.todo('%s-subtract' % logstring):
            
            # Add old model - ms:SUBTRACTED_DATA -> ms:SUBTRACTED_DATA
            logger.info('Add old DD-cal model to SUBTRACTED_DATA...')
            MSs.run('DPPP '+parset_dir+'/DPPP-predict.parset msin=$pathMS msin.datacolumn=SUBTRACTED_DATA msout.datacolumn=SUBTRACTED_DATA \
                    pre.operation=add pre.sourcedb='+d.get_skydb(0), \
                    log='$nameMS_add-'+logstring+'.log', commandType='DPPP')

            # Predict new model - ms:MODEL_DATA
            logger.info('Predict new DD-cal model in MODEL_DATA...')
            MSs.run('DPPP '+parset_dir+'/DPPP-predict.parset msin=$pathMS msin.datacolumn=DATA msout.datacolumn=MODEL_DATA \
                    pre.sourcedb='+d.get_skydb(-2), \
                    log='$nameMS_prenew-'+logstring+'.log', commandType='DPPP')

            # Store of FLAGS
            MSs.run('taql "update $pathMS set FLAG_BKP = FLAG"', \
                    log='$nameMS_taql-'+logstring+'.log', commandType='general')

            # Corrput now model - ms:MODEL_DATA -> MODEL_DATA
            logger.info('Corrupt ph...')
            MSs.run('DPPP '+parset_dir+'/DPPP-correct.parset msin=$pathMS msin.datacolumn=MODEL_DATA msout.datacolumn=MODEL_DATA \
                        cor.invert=False cor.parmdb='+d.get_h5parm('ph',-2)+' cor.correction=tec000', \
                        log='$nameMS_corrupt-'+logstring+'.log', commandType='DPPP')
            MSs.run('DPPP '+parset_dir+'/DPPP-correct.parset msin=$pathMS msin.datacolumn=MODEL_DATA msout.datacolumn=MODEL_DATA \
                        cor.invert=False cor.parmdb='+d.get_h5parm('ph',-2)+' cor.correction=phase000', \
                        log='$nameMS_corrupt-'+logstring+'.log', commandType='DPPP')

            if not d.get_h5parm('amp1',-2) is None:
                logger.info('Corrupt amp...')
                MSs.run('DPPP '+parset_dir+'/DPPP-correct.parset msin=$pathMS msin.datacolumn=MODEL_DATA msout.datacolumn=MODEL_DATA \
                       cor.invert=False cor.parmdb='+d.get_h5parm('amp1',-2)+' cor.correction=amplitude000', \
                       log='$nameMS_corrupt-'+logstring+'.log', commandType='DPPP') 
                MSs.run('DPPP '+parset_dir+'/DPPP-correct.parset msin=$pathMS msin.datacolumn=MODEL_DATA msout.datacolumn=MODEL_DATA \
                       cor.invert=False cor.parmdb='+d.get_h5parm('amp2',-2)+' cor.correction=amplitude000', \
                       log='$nameMS_corrupt-'+logstring+'.log', commandType='DPPP') 

            # Set MODEL_DATA = 0 where data are flagged, then unflag everything
            MSs.run('taql "update $pathMS set MODEL_DATA[FLAG] = 0"', \
                    log='$nameMS_taql-'+logstring+'.log', commandType='general')

            # Restore of FLAGS
            MSs.run('taql "update $pathMS set FLAG = FLAG_BKP"', \
                    log='$nameMS_taql-'+logstring+'.log', commandType='general')

            # Remove the ddcal again
            logger.info('Set SUBTRACTED_DATA = SUBTRACTED_DATA - MODEL_DATA...')
            MSs.run('taql "update $pathMS set SUBTRACTED_DATA = SUBTRACTED_DATA - MODEL_DATA"', \
                    log='$nameMS_taql-'+logstring+'.log', commandType='general')

            w.done('%s-subtract' % logstring)
        ### DONE

        ### TTESTTESTTEST: empty image
        if not os.path.exists('img/empty-%02i-%s-image.fits' % (dnum, logstring)):
            clean('%02i-%s' % (dnum, logstring), MSs, size=(fwhm*1.5,fwhm*1.5), res='normal', empty=True)
        ###

    ######################################################
    # full imaging

    # combine the h5parms
    combined_h5parm = 'ddcal/solutions/combined.h5'
    all_h5parms = [d.h5parms['ph'] for d in directions if 'ph' in d.h5parms.keys()] # phase solutions TODO: add amp
    for h5parmFile in all_h5parms:
        dirname = h5parmFile.split('-')[3]
        lib_h5.repoint(h5parmFile, dirname)

    lib_util.check_rm('ddcal/solutions/combined.h5')
    os.system('H5parm_collector.py -o '+combined_h5parm+' '+' '.join(all_h5parms))

    # prepare the aterms
    skymodel = 'ddcal/skymodels/skymodel00_cluster.txt'
    
    box = [phase_center[0], phase_center[1], phase_center[0], phase_center[1]] # [maxRA, minDec, minRA, maxDec]
    os.system('~/scripts/LiLF/scipts/make_aterm_images.py --soltabname gain000 --solsetname sol000 --cellsize_deg 0.1 --smooth_deg 0.1 \
            --bounds_deg %f\;%f\;%f\;%f --bounds_mid_deg %d\;%d --outroot ddcal/aterm/aterm_t --skymodel %s %s' % \
            (*box, *phase_center, skymodel, combined_h5parm) )

    # create aterm config file (ddcal/aterm/aterm.config)
    aterm_config_file = 'ddcal/aterm/aterm.config'
    with open(aterm_config_file, 'w') as file:  # Use file to refer to the file object
        file.write('aterms = [diagonal, beam]')
        file.write('diagonal.images = ['+' '.join(glob.glob('ddcal/aterm/aterm_t*fits'))+']')
        file.write('diagonal.window = tukey\n diagonal.update_interval  = 48.066724')
        file.write('beam.differential = true\n beam.update_interval = 120\n beam.usechannelfreq = true')

    # run the imager
    imagename = 'img/final-c'+str(cmaj)
    lib_util.run_wsclean(s, 'wsclean-c'+str(cmaj)+'.log', MSs.getStrWsclean(), name=imagename, size='6000 6000', save_source_list='', scale='5arcsec', \
                weight='briggs -0.3', niter=2000, no_update_model_required='', minuv_l=30, mgain=0.85, \
                multiscale='', multiscale_scale_bias=0.65, multiscale_scales='0,10,20,40,80',
                parallel_deconvolution=512, local_rms='', auto_threshold=0.5, auto_mask=1.5, \
                join_channels='', fit_spectral_pol=3, channels_out=12, deconvolution_channels=3, \
                temp_dir='./', pol='I', use_idg='', aterm_config=aterm_config_file, aterm_kernel_size=45, nmiter=4 )
    #wsclean -scale 0.0004166666666666667 -aterm-config ddcal/aterm/aterm.config -multiscale-scales 0 -size 1500 1500 -deconvolution-channels 4 -fits-mask /beegfs/rafferty/Data/LOFAR/Screens/Factor_sim/pipelines/image_1/sector_3/chunk9.ms.premask -auto-mask 3.6 -idg-mode hybrid -channels-out 12 -local-rms-window 50 -mgain 0.5 -minuv-l 80.0 -fit-spectral-pol 3 -maxuv-l 1000000.0 -weighting-rank-filter 3 -aterm-kernel-size 32 -temp-dir /tmp -name /beegfs/rafferty/Data/LOFAR/Screens/Factor_sim/pipelines/image_1/sector_3/chunk9.ms.image -padding 1.2 -pol I -multiscale-shape gaussian -auto-threshold 1.0 -local-rms-method rms-with-min -weight briggs -0.5 -niter 13635 -no-update-model-required -multiscale -fit-beam -reorder -save-source-list -local-rms -join-channels -use-idg -apply-primary-beam -nmiter 4

    mosaic_image = lib_img.Image(imagename+'-MFS-image.fits', userReg = userReg)
