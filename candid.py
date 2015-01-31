import numpy as np
from matplotlib import pyplot as plt
plt.ion() # interactive mode
_fitsLoaded=False
try:
    from astropy.io import fits
    _fitsLoaded=True
except:
    try:
        import pyfits as fits
        _fitsLoaded=True
    except:
        pass

if not _fitsLoaded:
    print 'ERROR: astropy.io.fits or pyfits required!'

import time
import scipy.special
import scipy.interpolate
import scipy.stats
import multiprocessing
import os

#__version__ = '0.1 | 2014/11/25'
#__version__ = '0.2 | 2015/01/07' # big clean
#__version__ = '0.3 | 2015/01/14' # add Alex contrib and FLAG taken into account
__version__ = '0.4 | 2015/01/30' # modified bandwidth semaring handling

print """
===================== This is CANDID ===================================
[C]ompanion [A]nalysis and [N]on-[D]etection in [I]nterferometric [D]ata
                https://github.com/amerand/CANDID
========================================================================"""
print ' | version:', __version__

# -- some general parameters:
CONFIG = {#'n_smear': 3, # number of channels for bandwidth smearing -> OBSOLETE!
          'default cmap':'cubehelix', # color map used
          'longExecWarning': 180, # in seconds
          'suptitle':True, # display over head title
          }

def variables():
    print ' > global parameters (can be updated):'
    for k in CONFIG.keys():
        print "  CONFIG['%s']"%k, CONFIG[k]
    # print ' | CONFIG['n_smear']=', CONFIG['n_smear'], ':number of channels for bandwidth smearing '
    # print ' |           (the larger, the more accurate but the slower)'
    # print ' | cmap=', "'"+cmap+"'", ': default color map'
    # print ' | longExecWarning=', longExecWarning, ': maximum allowed computation run, in seconds'
    print '                            _oOo_\n'
    return

variables()

def Verify():
    """
    Checks the accuracy of the formula in the code
    """
    plt.close('all')
    filename = 'AXCir.oifits'
    print '\nLOADING:', filename, '(rmin=2 mas, rmax=20 mas)'
    a = Open(filename, rmin=2, rmax=20)

    diam = 0.9
    p = {'x':12.0, 'y':12.0, 'f':0.00, 'diam*':diam, 'dwavel':a.dwavel}
    print '\nreplace data with synthetic ones for UD diam=%3.2f mas, without companion'%p['diam']

    s = _modelObservables(a._rawData, p)
    n = 0
    for k in range(len(a._rawData)):
        tmp = list(a._rawData[k])
        # -- replace data with synthetic ones
        tmp[-2] = s[n:n+tmp[-2].size].reshape(tmp[-2].shape)
        # -- add noise:
        noise = a._rawData[k][-1]
        if a._rawData[k][0] == 't3': # KLUDGE!
            print '@@@ T3 noise KLUDGE! @@@'
            noise = np.minimum(noise, 0.1*tmp[-2])
            tmp[-1] = noise

        noise *= np.random.randn(noise.shape[0], noise.shape[1])
        tmp[-2] += noise
        a._rawData[k] = tuple(tmp)
        n += a._rawData[k][-2].size
    a._chi2Data = a._rawData

    # print '\nFITTING UD, V2 only'
    # a.observables = ['v2']; a.fitUD()

    # print '\nFITTING UD, T3 only'
    # a.observables = ['t3']; a.fitUD()

    # print '\nCHI2 UD, cp'
    # a.observables = ['cp']; a.fitUD(forcedDiam=diam)

    for i,o in enumerate(['v2', 'cp', 't3']):
        print '\nCHI2MAP'
        a.observables = [o]
        a.chi2Map(.5, fratio=0.01, fig=i+1)

    print '\nCHI2MAP'
    a.observables = ['v2', 'cp', 't3']
    a.chi2Map(.5, fratio=0.01, fig=4)

    print '\nCLOSING:', filename
    a.close()
    return

# -- some general functions:
def _Vud(base, diam, wavel):
    """
    Complex visibility of a uniform disk for parameters:
    - base in m
    - diam in mas
    - wavel in um
    """
    c = np.pi/180/3600000.*1e6
    x = c*diam*base/wavel
    x += 1e-6*(x==0)
    x *= np.pi
    res = np.array(2*scipy.special.jv(1, x)/(x))
    return res

def _Vbin(uv, param):
    """
    Analytical complex visibility of a binary composed of a uniform
    disk diameter and an unresolved source. "param" is a dictionnary
    containing:

    'diam*': in mas
    'wavel': in um
    'x, y': in mas
    'f': flux ratio -> takes the absolute value
    """
    if 'f' in param:
        f = np.abs(param['f'])
    else:
        f = param['f']
    c = np.pi/180/3600000.*1e6
    B = np.sqrt(uv[0]**2+uv[1]**2)
    Vstar = _Vud(B, param['diam*'], param['wavel'])
    phi = 2*np.pi*c*(uv[0]*param['x']+uv[1]*param['y'])/param['wavel']
    Vcomp = np.exp(-1j*phi) # assumes it is unresolved.
    # -- Alex's formula to take into account the bandwith smearing
    # -- >>> Only works for V and V2, not for CP?
    if 'dwavel' in param.keys():
        x = phi*param['dwavel']/param['wavel']/2.
        x += 1e-6*(x==0)
        f *= np.abs(np.sin(x)/x)
    res = (Vstar + f*Vcomp)/(1.0+f)
    return res

def _modelObservables(obs, param):
    """
    model observables contained in "obs".
    param -> see _Vbin

    Observations are entered as:
    obs = [('v2;ins', u, v, wavel, ...),
           ('cp;ins', u1, v1, u2, v2, wavel, ...),
           ('t3;ins', u1, v1, u2, v2, wavel, ...)]
    each tuple can be longer, the '...' part will be ignored

    units: u,v in m; wavel in um

    width of the wavelength channels in param:
    - a global "dwavel" is defined, the width of the pixels
    - "dwavel;ins" average value per intrument

    for CP and T3, the third u,v coordinate is computed as u1+u2, v1+v2
    """
    global CONFIG
    #c = np.pi/180/3600000.*1e6
    res = [0.0 for o in obs]
    # -- copy parameters:
    tmp = {k:param[k] for k in param.keys()}
    tmp['f'] = np.abs(tmp['f'])
    for i, o in enumerate(obs):
        if 'dwavel' in param.keys():
            dwavel = param['dwavel']
        elif 'dwavel;'+o[0].split(';')[1] in param.keys():
            dwavel = param['dwavel;'+o[0].split(';')[1]]
        else:
            dwavel = None

        tmp = {k:tmp[k] for k in param.keys() if not k.startswith('dwavel')}
        if not dwavel is None:
            tmp['dwavel'] = dwavel

        # -- force using the monochromatic, since smearing is in _VBin
        dwavel=None;

        if o[0].split(';')[0]=='v2':
            tmp['wavel'] = o[3]
            if dwavel is None: # -- monochromatic
                res[i] = np.abs(_Vbin([o[1], o[2]], tmp))**2
            else: # -- bandwidth smearing manually
                wl0 = tmp['wavel']
                for x in np.linspace(-0.5, 0.5, CONFIG['n_smear']):
                    tmp['wavel'] = wl0 + x*dwavel
                    res[i] += np.abs(_Vbin([o[1], o[2]], tmp))**2
                tmp['wavel'] = wl0
                res[i] /= float(CONFIG['n_smear'])

        elif o[0].split(';')[0]=='cp' or o[0].split(';')[0]=='t3':
            tmp['wavel'] = o[5]
            if dwavel is None: # -- monochromatic
                t3 = _Vbin([o[1], o[2]], tmp)*\
                     _Vbin([o[3], o[4]], tmp)*\
                     np.conj(_Vbin([o[1]+o[3], o[2]+o[4]], tmp))
            else: # -- bandwidth smearing manually
                wl0 = tmp['wavel']
                t3 = 0.0
                for x in np.linspace(-0.5, 0.5, CONFIG['n_smear']):
                    tmp['wavel'] = wl0 + x*dwavel
                    _t3 = _Vbin([o[1], o[2]], tmp)*\
                          _Vbin([o[3], o[4]], tmp)*\
                          np.conj(_Vbin([o[1]+o[3], o[2]+o[4]], tmp))
                    t3 += _t3/float(CONFIG['n_smear'])
                tmp['wavel'] = wl0

            if o[0].split(';')[0]=='cp':
                res[i] = np.angle(t3)
            elif o[0].split(';')[0]=='t3':
                res[i] = np.absolute(t3)

        else:
            print 'ERROR: unreckognized observable:', o[0]
    res2 = np.array([])
    for r in res:
        res2 = np.append(res2, r.flatten())
    return res2

def _nSigmas(chi2r_TEST, chi2r_TRUE, NDOF):
    """
    - chi2r_TEST is the hypothesis we test
    - chi2r_TRUE is what we think is what described best the data
    - NDOF: numer of degres of freedom

    chi2r_TRUE <= chi2r_TEST

    returns the nSigma detection
    """
    p = scipy.stats.chi2.cdf(NDOF, NDOF*chi2r_TEST/chi2r_TRUE)
    log10p = np.log10(np.maximum(p, 1e-161)) ### Alex: 50 sigmas max
    #p = np.maximum(p, -100)
    res = np.sqrt(scipy.stats.chi2.ppf(1-p,1))
    # x = np.logspace(-15,-12,100)
    # c = np.polyfit(np.log10(x), np.sqrt(scipy.stats.chi2.ppf(1-x,53)), 1)
    c = np.array([-0.25028407,  9.66640457])
    if isinstance(res, np.ndarray):
        res[log10p<-15] = np.polyval(c, log10p[log10p<-15])
        res = np.nan_to_num(res)
        res += 90*(res==0)
    else:
        if log10p<-15:
            res =  np.polyval(c, log10p)
        if np.isnan(res):
            res = 90.
    return res

def _injectCompanionData(data, delta, param):
    """
    Inject analytically a companion defined as 'param' in the 'data' using 'delta'. 'delta' contains the corresponding V2 for T3 and CP first order calculations.

    data and delta have same length
    """
    global CONFIG
    res = [[x if isinstance(x, str) else x.copy() for x in d] for d in data] # copy the data
    c = np.pi/180/3600000.*1e6
    for i,d in enumerate(res):
        if 'dwavel' in param:
            dwavel = param['dwavel']
        elif 'dwavel;'+d[0].split(';')[1] in param:
            dwavel = param['dwavel;'+d[0].split(';')[1]]
        else:
            dwavel = None
        if d[0].split(';')[0]=='v2':
            if dwavel is None:
                phi = c*2*np.pi*(d[1]*param['x']+d[2]*param['y'])/d[3]
                d[-2] = (d[-2] + 2*param['f']*np.sqrt(np.abs(d[-2]))*np.cos(phi) + param['f']**2)/(1+param['f'])**2
            else:
                # -- assumes bandwidthSmearing is a spectral Resolution:
                tmp = 0.0
                for x in np.linspace(-0.5, 0.5, CONFIG['n_smear'])*dwavel:
                    phi = c*2*np.pi*(d[1]*param['x']+d[2]*param['y'])/(d[3]+x)
                    tmp += (d[-2] + 2*param['f']*np.sqrt(np.abs(d[-2]))*np.cos(phi) + param['f']**2)/(1+param['f'])**2
                d[-2] = tmp/float(CONFIG['n_smear'])

        if d[0].split(';')[0]=='cp':
            if dwavel is None:
                phi0 = c*2*np.pi*(d[1]*param['x']+d[2]*param['y'])/d[5]
                phi1 = c*2*np.pi*(d[3]*param['x']+d[4]*param['y'])/d[5]
                phi2 = c*2*np.pi*((d[1]+d[3])*param['x']+(d[2]+d[4])*param['y'])/d[5]
                # -- Antoine's original:
                # d[-2] -= param['f']*(np.sin(phi0)/delta[i][0]+
                #                      np.sin(phi1)/delta[i][1]-
                #                      np.sin(phi2)/delta[i][2])
                # -- from Alex:
                tmp = 1 + param['f']*( np.exp(-1j*phi2)/delta[i][2] + np.exp(1j*phi1)/delta[i][1] + np.exp(1j*phi0)/delta[i][0] )
                d[-2] -= np.angle(tmp)
            else:
                # -- assumes bandwidthSmearing is a spectral Resolution:
                tmp = 0.0 + 0.0j
                for x in np.linspace(-0.5, 0.5, CONFIG['n_smear'])*dwavel:
                    phi0 = c*2*np.pi*(d[1]*param['x']+d[2]*param['y'])/(d[5]+x)
                    phi1 = c*2*np.pi*(d[3]*param['x']+d[4]*param['y'])/(d[5]+x)
                    phi2 = c*2*np.pi*((d[1]+d[3])*param['x']+(d[2]+d[4])*param['y'])/(d[5]+x)
                    # -- Antoine's:
                    # tmp += param['f']*(np.sin(phi0)/delta[i][0]+
                    #                    np.sin(phi1)/delta[i][1]-
                    #                    np.sin(phi2)/delta[i][2])
                    # -- Alex:
                    tmp += 1 + param['f']*(np.exp(-1j*phi2)/delta[i][2] + np.exp(1j*phi1)/delta[i][1] + np.exp(1j*phi0)/delta[i][0])

                # -- Antoine's:
                #d[-2] -= tmp/float(CONFIG['n_smear'])
                # -- Alex's:
                d[-2] -= np.angle(tmp/float(CONFIG['n_smear']))

        if d[0].split(';')[0]=='t3':
            if dwavel is None:
                phi0 = c*2*np.pi*(d[1]*param['x']+d[2]*param['y'])/d[5]
                phi1 = c*2*np.pi*(d[3]*param['x']+d[4]*param['y'])/d[5]
                phi2 = c*2*np.pi*((d[1]+d[3])*param['x']+(d[2]+d[4])*param['y'])/d[5]
                # -- Antoine's:
                # d[-2] += 2*param['f']*(np.cos(phi0)/delta[i][0]+
                #                        np.cos(phi1)/delta[i][1]-
                #                        np.cos(phi2)/delta[i][2])/(1+param['f'])**3
                # -- Alex's:
                tmp = 1 + param['f']*(np.exp(-1j*phi2)/delta[i][2] +
                                      np.exp(1j*phi1)/delta[i][1] +
                                      np.exp(1j*phi0)/delta[i][0])
                d[-2] *= np.abs(tmp) /(1+param['f'])**3
            else:
                # -- assumes bandwidthSmearing is a sepctral Resolution:
                tmp = 0.0 + 0.0j
                for x in np.linspace(-0.5, 0.5, CONFIG['n_smear'])*dwavel:
                    phi0 = c*2*np.pi*(d[1]*param['x']+d[2]*param['y'])/(d[5]+x)
                    phi1 = c*2*np.pi*(d[3]*param['x']+d[4]*param['y'])/(d[5]+x)
                    phi2 = c*2*np.pi*((d[1]+d[3])*param['x']+(d[2]+d[4])*param['y'])/(d[5]+x)
                    # -- Antoine's
                    # tmp += 2*param['f']*(np.cos(phi0)/delta[i][0]+
                    #                    np.cos(phi1)/delta[i][1]-
                    #                    np.cos(phi2)/delta[i][2])/(1+param['f'])**3
                    # -- Alex's:
                    tmp += 1+ param['f']*( np.exp(-1j*phi2)/delta[i][2] + np.exp(1j*phi1)/delta[i][1] + np.exp(1j*phi0)/delta[i][0] )
                # -- Antoine's
                #d[-2] += tmp/float(CONFIG['n_smear'])
                # -- Alex's:
                d[-2] *= np.abs(tmp/float(CONFIG['n_smear'])) /(1+param['f'])**3
    return res

def _fitFunc(param, chi2Data, observables, fitAlso=None):
    _meas, _errs = np.array([]), np.array([])
    for c in chi2Data:
        if c[0].split(';')[0] in observables:
            _meas = np.append(_meas, c[-2].flatten())
            _errs = np.append(_errs, c[-1].flatten())
    _errs += _errs==0. # remove bad point in a dirty way
    fitOnly=[]
    if param['f']!=0:
        fitOnly.extend(['x', 'y', 'f'])
    if 'v2' in observables or 't3' in observables:
       fitOnly.append('diam*')
    if not fitAlso is None:
        fitOnly.extend(fitAlso)
    res = _dpfit_leastsqFit(_modelObservables,
                            filter(lambda c: c[0].split(';')[0] in observables, chi2Data),
                            param, _meas, _errs, fitOnly = fitOnly)
    if '_k' in param.keys():
        res['_k'] = param['_k']
    if 'diam*' in res['best'].keys():
        res['best']['diam*'] = np.abs(res['best']['diam*'])
    return res

def _chi2Func(param, chi2Data, observables):
    """
    Returns the chi2r comparing model of parameters "param" and data "chi2Data", only
    considering "observables" (such as v2, cp, t3)
    """
    _meas, _errs = np.array([]), np.array([])
    for c in chi2Data:
        if c[0].split(';')[0] in observables:
            _meas = np.append(_meas, c[-2].flatten())
            _errs = np.append(_errs, c[-1].flatten())
    _errs += _errs==0. # remove bad point in a dirty way
    res = (_meas-_modelObservables(filter(lambda c: c[0].split(';')[0] in observables, chi2Data), param))**2/_errs**2
    res = np.nan_to_num(res) # FLAG == TRUE are nans in the data
    res = np.mean(res)
    if '_i' in param.keys() and '_j' in param.keys():
        return param['_i'], param['_j'], res
    else:
        #print 'test:', res
        return res

def _detectLimit(param, chi2Data, observables, delta=None, method='injection'):
    """
    Returns the flux ratio for which the chi2 ratio between binary and UD is 3 sigmas.

    Uses the postion and diameter given in "Param" and only varies the flux ratio

    - method=="Absil", uses chi2_BIN/chi2_UD, assuming chi2_UD is the best model
    - otherwise, uses chi2_UD/chi2_BIN, after injecting a companion
    """
    if method!='Absil':
        method = 'injection'
    fr, nsigma = [], []
    mult = 1.4
    cond = True
    if method=='Absil':
        tmp = {k:param[k] if k!='f' else 0.0 for k in param.keys()}
        # -- reference chi2 for UD
        chi2_0 = _chi2Func(tmp, chi2Data, observables)[-1]

    ndata = np.sum([c[-1].size for c in chi2Data if c[0].split(';')[0] in observables])
    n = 0
    while cond:
        if method=='Absil':
            fr.append(param['f'])
            nsigma.append(_nSigmas(_chi2Func(param, chi2Data, observables)[-1], chi2_0, ndata))
        else:
            fr.append(param['f'])
            data = _injectCompanionData([chi2Data[k] for k in range(len(chi2Data))
                                                if chi2Data[k][0].split(';')[0]  in observables],
                                        [delta[k] for k in range(len(delta))
                                                if chi2Data[k][0].split(';')[0] in observables],
                                        param)
            tmp = {k:param[k] if k!='f' else 0.0 for k in param.keys()}
            a, b = _chi2Func(tmp, data, observables)[-1], _chi2Func(param, data, observables)[-1]
            nsigma.append(_nSigmas(a, b, ndata))

        # -- Newton method:
        #print nsigma[-1]
        if len(fr)==1:
            if nsigma[-1]<3:
                param['f'] *= mult
            else:
                param['f'] /= mult
        else:
            #print 'DEBUG', mult, fr[-2:], nsigma[-2:], param['f']
            if nsigma[-1]<3 and nsigma[-2]<3:
                param['f'] *= mult
            elif nsigma[-1]>=3 and nsigma[-2]>=3:
                param['f'] /= mult
            elif nsigma[-1]<3 and nsigma[-2]>=3:
                mult /= 1.5
                param['f'] *= mult
            elif nsigma[-1]>=3 and nsigma[-2]<3:
                mult /= 1.5
                param['f'] /= mult
        n+=1
        # -- stopping:
        if len(fr)>1 and any([s>3 for s in nsigma]) and any([s<3 for s in nsigma]):
            cond = False
        if n>10:
            cond = False

    fr, nsigma = np.array(fr), np.array(nsigma)
    fr = fr[np.argsort(nsigma)]
    nsigma = nsigma[np.argsort(nsigma)]
    if '_i' in param.keys() and '_j' in param.keys():
        return param['_i'], param['_j'], np.interp(3, nsigma, fr)
    else:
        return np.interp(3, nsigma, fr)

# == The main class
class Open:
    global CONFIG, _ff2_data
    def __init__(self, filename, rmin=0.0, rmax=None, Ncores=None):
        """
        - filename: an OIFITS file
        - rmin, rmax: minimum and maximum radius (in mas) for plots and search
        - Ncores: max number of core used, if None (default) will use all
          available cores but one.

        load OIFITS file assuming one target, one OI_VIS2, one OI_T3 and one WAVE table
        """
        print ' > loading file', filename
        self.filename = filename
        self._loadOifitsData()
        self.observables = list(set([c[0].split(';')[0] for c in self._rawData]))
        print ' | observables available: [',
        print ', '.join(["'"+o+"'" for o in self.observables])+']'
        self._chi2Data = self._rawData
        self.rmin = rmin
        self.rmax = rmax
        if rmax is None:
            print " | WARNING! you should define 'rmax'"
        self.diam=None
        self.compParam = None
        self.compUncer = None
        self.Ncores = Ncores
        # if diam is None and\
        #     ('v2' in self.observables or 't3' in self.observables):
        #     print ' | no diameter given, trying to estimate it...'
        #     self.fitUD()
        # else:
        #     self.diam = diam
    def fitUD(self, forcedDiam=None):
        """
        FIT UD diameter model to the data (if )
        """
        # -- fit diameter if possible
        if not forcedDiam is None:
            self.diam = forcedDiam

        if forcedDiam is None and \
            ('v2' in self.observables or 't3' in self.observables) and\
            ('v2' in [c[0].split(';')[0] for c in self._chi2Data] or
             't3' in [c[0].split(';')[0] for c in self._chi2Data]):
            tmp = {'x':0, 'y':0, 'f':0, 'diam*':1.0}
            for _k in self.dwavel.keys():
                tmp['dwavel;'+_k] = self.dwavel[_k]
            fit_0 = _fitFunc(tmp, self._chi2Data, self.observables)

            print ' > UD Fit'
            self.chi2_UD = fit_0['chi2']
            print ' | best fit diameter: %5.3f +- %5.3f mas'%(fit_0['best']['diam*'],
                                                           fit_0['uncer']['diam*'])
            self.diam = fit_0['best']['diam*']
            self.ediam = fit_0['uncer']['diam*']
            print ' | chi2 UD = %4.3f'%self.chi2_UD
        elif not self.diam is None:
            print ' > UD CHI2 (no fit!)'
            print ' | Chi2 UD for diam=%4.3fmas'%self.diam
            tmp = {'x':0, 'y':0, 'f':0, 'diam*':1.0}
            for _k in self.dwavel.keys():
                tmp['dwavel;'+_k] = self.dwavel[_k]
            fit_0 = _fitFunc(tmp, self._chi2Data, self.observables)
            self.chi2_UD = _chi2Func(tmp, self._chi2Data, self.observables)
            self.ediam = np.nan
            print ' |  chi2 UD = %4.3f'%self.chi2_UD
        else:
            print " | WARNING: a UD cannot be determined, and a valid 'diam' is not defined for Chi2 computation. ASSUMING CHI2UD=1.0"
            self.diam = None
            self.ediam = np.nan
            self.chi2_UD = 1.0
        return
    def _loadOifitsData(self):
        """
        Note that CP are stored in radians, not degrees like in the OIFITS!
        """
        self._fitsHandler = fits.open(self.filename)
        self._dataheader={}
        for k in ['X','Y','F']:
            try:
                self._dataheader[k] = self._fitsHandler[0].header['INJCOMP'+k]
            except:
                pass
        # -- load wavelength:
        self.wavel = {}
        self.dwavel = {} # -- average width of the spectral channels
        self.all_dwavel = {} # -- width of all the spectral channels
        for hdu in self._fitsHandler[1:]:
            if hdu.header['EXTNAME']=='OI_WAVELENGTH':
                self.wavel[hdu.header['INSNAME']] = hdu.data['EFF_WAVE']*1e6 # in um
                self.all_dwavel[hdu.header['INSNAME']] = np.abs(np.gradient(hdu.data['EFF_WAVE']*1e6))
                self.all_dwavel[hdu.header['INSNAME']] *= 2. # assume the limit is not the pixel
                self.dwavel[hdu.header['INSNAME']]=np.mean(self.all_dwavel[hdu.header['INSNAME']])

        # -- load all data:
        self._rawData, self._delta = [], []
        for hdu in self._fitsHandler[1:]:
            if hdu.header['EXTNAME']=='OI_T3':
                ins = hdu.header['INSNAME']
                # -- CP
                data = hdu.data['T3PHI']*np.pi/180
                data[hdu.data['FLAG']] = np.nan # we'll deal with that later...
                self._rawData.append(('cp;'+ins,
                      hdu.data['U1COORD'][:,None]+0*self.wavel[ins][None,:],
                      hdu.data['V1COORD'][:,None]+0*self.wavel[ins][None,:],
                      hdu.data['U2COORD'][:,None]+0*self.wavel[ins][None,:],
                      hdu.data['V2COORD'][:,None]+0*self.wavel[ins][None,:],
                      self.wavel[ins][None,:]+0*hdu.data['V1COORD'][:,None],
                      hdu.data['MJD'][:,None]+0*self.wavel[ins][None,:],
                      ins,
                      data,
                      hdu.data['T3PHIERR']*np.pi/180))
                # -- T3
                data = hdu.data['T3AMP']
                data[hdu.data['FLAG']] = np.nan # we'll deal with that later...
                self._rawData.append(('t3;'+ins,
                      hdu.data['U1COORD'][:,None]+0*self.wavel[ins][None,:],
                      hdu.data['V1COORD'][:,None]+0*self.wavel[ins][None,:],
                      hdu.data['U2COORD'][:,None]+0*self.wavel[ins][None,:],
                      hdu.data['V2COORD'][:,None]+0*self.wavel[ins][None,:],
                      self.wavel[ins][None,:]+0*hdu.data['V1COORD'][:,None],
                      hdu.data['MJD'][:,None]+0*self.wavel[ins][None,:], ins,
                      data,
                      hdu.data['T3AMPERR']))
            if hdu.header['EXTNAME']=='OI_VIS2':
                ins = hdu.header['INSNAME']
                data = hdu.data['VIS2DATA']
                data[hdu.data['FLAG']] = np.nan # we'll deal with that later...
                self._rawData.append(('v2;'+ins,
                      hdu.data['UCOORD'][:,None]+0*self.wavel[ins][None,:],
                      hdu.data['VCOORD'][:,None]+0*self.wavel[ins][None,:],
                      self.wavel[ins][None,:]+0*hdu.data['VCOORD'][:,None],
                      hdu.data['MJD'][:,None]+0*self.wavel[ins][None,:],
                      ins,
                      data,
                      hdu.data['VIS2ERR']))

        # -- compute a flatten version of all V2:
        allV2 = {'u':np.array([]), 'v':np.array([]), 'mjd':np.array([]),
                 'wl':np.array([]), 'v2':np.array([])}
        for r in filter(lambda x: x[0].split(';')[0]=='v2', self._rawData):
            allV2['u'] = np.append(allV2['u'], r[1].flatten())
            allV2['v'] = np.append(allV2['v'], r[2].flatten())
            allV2['wl'] = np.append(allV2['wl'], r[3].flatten())
            allV2['mjd'] = np.append(allV2['mjd'], r[4].flatten())
            allV2['v2'] = np.append(allV2['v2'], r[-2].flatten())

        # -- delta for approximation, very long!
        self._delta = []
        for r in self._rawData:
            if r[0].split(';')[0] in ['cp', 't3']:
                # -- this will contain the delta for this r
                vis1, vis2, vis3 = np.zeros(r[-2].shape), np.zeros(r[-2].shape), np.zeros(r[-2].shape)
                for i in range(r[-2].shape[0]):
                    for j in range(r[-2].shape[1]):
                        # -- find Vis
                        k1 = np.argmin((allV2['u']-r[1][i,j])**2+
                                       (allV2['v']-r[2][i,j])**2+
                                       (allV2['wl']-r[5][i,j])**2+
                                       ((allV2['mjd']-r[6][i,j])/10000.)**2)
                        if not np.isnan(r[-2][i,j]):
                            vis1[i,j] = np.sqrt(allV2['v2'][k1])
                        k2 = np.argmin((allV2['u']-r[3][i,j])**2+
                                       (allV2['v']-r[4][i,j])**2+
                                       (allV2['wl']-r[5][i,j])**2+
                                       ((allV2['mjd']-r[6][i,j])/10000.)**2)
                        if not np.isnan(r[-2][i,j]):
                            vis2[i,j] = np.sqrt(allV2['v2'][k2])
                        k3 = np.argmin((allV2['u']-r[1][i,j]-r[3][i,j])**2+
                                       (allV2['v']-r[2][i,j]-r[4][i,j])**2+
                                       (allV2['wl']-r[5][i,j])**2+
                                       ((allV2['mjd']-r[6][i,j])/10000.)**2)
                        if not np.isnan(r[-2][i,j]):
                            vis3[i,j] = np.sqrt(allV2['v2'][k3])
                self._delta.append((vis1, vis2, vis3))
            if r[0].split(';')[0] == 'v2':
                self._delta.append(None)
        #print [r[0] for r in self._rawData]
        return
    def ndata(self):
        tmp = 0
        for c in self._chi2Data:
            if c[0].split(';')[0] in self.observables:
                tmp += len(c[-1].flatten())
        return tmp
    def close(self):
        self._fitsHandler.close()
        return
    def _pool(self):
        if self.Ncores is None:
            self.Ncores = max(multiprocessing.cpu_count()-1,1)
        return multiprocessing.Pool(self.Ncores)
    def estimateRunTime(self, function, params):
        # -- estimate how long it will take, in two passes
        p = self._pool()
        t = time.time()
        for m in params:
            p.apply_async(function, m)
        p.close()
        p.join()
        return (time.time()-t)/len(params)
    def _cb_chi2Map(self, r):
        """
        callback function for chi2Map()
        """
        try:
            self.mapChi2[r[1], r[0]] = r[2]
            # -- completed / to be computed
            f = np.sum(self.mapChi2>0)/float(np.sum(self.mapChi2>=0))
            if f>self._prog:
                n = int(50*f)
                print '\033[F',
                print '|'+'='*(n+1)+' '*(50-n)+'|',
                print '%2d%%'%(int(100*f)),
                self._progTime[1] = time.time()
                print '%3d s remaining'%(int((self._progTime[1]-self._progTime[0])/f*(1-f)))
                self._prog = max(self._prog+0.01, f+0.01)
        except:
            print 'did not work'
        return
    def chi2Map(self, step, fratio=None, addCompanion=None, removeCompanion=None, fig=0, diam=None):
        """
        Performs a chi2 map between rmin and rmax (should be defined) with step
        "step". The diameter is taken as the best fit UD diameter (biased if
        the data indeed contain a companion!).

        If 'addCompanion' or 'removeCompanion' are defined, a companion will
        be analytically added or removed from the data. define the companion
        as {'x':mas, 'y':mas, 'f':fratio}. 'f' will be forced to be positive.
        """

        # --
        try:
            N = int(np.ceil(2*self.rmax/step))
        except:
            print 'ERROR: you should define rmax first!'
            return

        print ' > observables:', self.observables
        if fratio is None:
            print ' > fratio is None -> using 0.01 (==1%)'
            fratio = 0.01

        if not addCompanion is None:
            tmp = {k:addCompanion[k] for k in addCompanion.keys()}
            tmp['f'] = np.abs(tmp['f'])
            self._chi2Data = _injectCompanionData(self._rawData, self._delta, tmp)
        elif not removeCompanion is None:
            tmp = {k:removeCompanion[k] for k in removeCompanion.keys()}
            tmp['f'] = -np.abs(tmp['f'])
            self._chi2Data = _injectCompanionData(self._rawData, self._delta, tmp)
        else:
            self._chi2Data = self._rawData

        self.fitUD(forcedDiam=diam)

        # -- prepare the grid
        allX = np.linspace(-self.rmax, self.rmax, N)
        allY = np.linspace(-self.rmax, self.rmax, N)
        self.mapChi2 = np.zeros((N,N))
        self.mapChi2[allX[None,:]**2+allY[:,None]**2 > self.rmax**2] = -1
        self.mapChi2[allX[None,:]**2+allY[:,None]**2 < self.rmin**2] = -1
        self._prog = 0.0
        self._progTime = [time.time(), time.time()]

        # -- parallel treatment:
        print ' | Computing Map %dx%d'%(N, N),
        # -- estimate how long it will take, in two passes
        params, Ntest = [], 10*max(multiprocessing.cpu_count()-1,1)
        for i in range(Ntest):
            o = np.random.rand()*2*np.pi
            tmp = {'x':np.cos(o)*(self.rmax+self.rmin),
                      'y':np.sin(o)*(self.rmax+self.rmin),
                      'f':0.01, 'diam*':self.diam}
            for _k in self.dwavel.keys():
                tmp['dwavel;'+_k] = self.dwavel[_k]
            params.append((tmp,self._chi2Data, self.observables))
        est = self.estimateRunTime(_chi2Func, params)
        est *= np.sum(self.mapChi2>=0)
        print '... it should take about %d seconds'%(int(est))
        if not CONFIG['longExecWarning'] is None and\
             est>CONFIG['longExecWarning']:
            print " > WARNING: this will take too long. "
            print " | Increase CONFIG['longExecWarning'] if you want to run longer computations."
            print " | set it to 'None' and the warning will disapear... at your own risks!"
            return
        print ''
        # -- done estimating time

        # -- compute actual grid:
        t = time.time()
        p = self._pool()
        for i,x in enumerate(allX):
            for j,y in enumerate(allY):
                if self.mapChi2[j,i]==0:
                    params = {'x':x, 'y':y, 'f':fratio, 'diam*':self.diam,
                              '_i':i, '_j':j,}
                    for _k in self.dwavel.keys():
                        params['dwavel;'+_k] = self.dwavel[_k]

                    # -- multi-thread:
                    p.apply_async(_chi2Func, (params, self._chi2Data, self.observables),
                                  callback=self._cb_chi2Map)
                    # -- single thread:
                    #self._cb_chi2Map(_chi2Func(params, self._chi2Data, self.observables))
        p.close()
        p.join()
        #print 'it actually took %d seconds'%(time.time()-t)

        # -- take care of unfitted zone, for esthetics
        self.mapChi2[self.mapChi2<=0] = self.chi2_UD
        X, Y = np.meshgrid(allX, allY)
        x0 = X.flatten()[np.argmin(self.mapChi2.flatten())]
        y0 = Y.flatten()[np.argmin(self.mapChi2.flatten())]
        s0 = _nSigmas(self.chi2_UD,
                       np.minimum(np.min(self.mapChi2), self.chi2_UD),
                       self.ndata())
        print ' | chi2 Min: %5.2f'%(np.min(self.mapChi2))
        print ' | at X,Y  : %6.2f, %6.2f mas'%(x0, y0)
        print ' | Nsigma  : %5.2f'%s0

        plt.close(fig)
        if CONFIG['suptitle']:
            plt.figure(fig, figsize=(12,5.8))
            plt.subplots_adjust(top=0.78, bottom=0.08,
                                left=0.08, right=0.99, wspace=0.10)
            title = "CANDID: $\chi^2$ Map for f$_\mathrm{ratio}$=%3.1f%% and "%(100*fratio)
            if self.ediam>0:
                title += r'fitted $\theta_\mathrm{UD}=%4.3f$ mas.'%(self.diam)
            else:
                title += r'fixed $\theta_\mathrm{UD}=%4.3f$ mas.'%(self.diam)
            title += ' Using '+', '.join(self.observables)
            title += '\n'+os.path.basename(self.filename)
            plt.suptitle(title, fontsize=14, fontweight='bold')
        else:
            plt.figure(fig, figsize=(12,5.4))
            plt.subplots_adjust(top=0.88, bottom=0.10,
                                left=0.08, right=0.99, wspace=0.10)

        ax1 = plt.subplot(121)
        tit = '$\chi^2_\mathrm{BIN}/\chi^2_\mathrm{UD}$ with $\chi^2_\mathrm{UD}$='
        if self.chi2_UD>0.05:
            tit += '%4.2f'%(self.chi2_UD)
        else:
            tit += '%4.2e'%(self.chi2_UD)

        plt.title(tit)
        plt.pcolormesh(X, Y, self.mapChi2/self.chi2_UD, cmap=CONFIG['default cmap'])
        plt.colorbar(format='%0.2f')
        plt.xlabel(r'$\Delta \alpha\, \rightarrow$ E (mas)')
        plt.ylabel(r'$\Delta \delta\, \rightarrow$ N (mas)')
        plt.xlim(self.rmax, -self.rmax)
        plt.ylim(-self.rmax, self.rmax)

        plt.subplot(122, sharex=ax1, sharey=ax1)
        plt.title('detection ($\sigma$)')
        plt.pcolormesh(X, Y,
                       _nSigmas(self.chi2_UD,
                                np.minimum(self.mapChi2, self.chi2_UD),
                                self.ndata()),
                       cmap=CONFIG['default cmap'])
        plt.colorbar(format='%0.2f')
        plt.xlabel(r'$\Delta \alpha\, \rightarrow$ E (mas)')
        plt.xlim(self.rmax, -self.rmax)
        plt.ylim(-self.rmax, self.rmax)
        ax1.set_aspect('equal', 'datalim')
        # -- make a crosshair around the minimum:
        plt.plot([x0, x0], [y0-0.05*self.rmax, y0-0.1*self.rmax], '-r', alpha=0.5, linewidth=2)
        plt.plot([x0, x0], [y0+0.05*self.rmax, y0+0.1*self.rmax], '-r', alpha=0.5, linewidth=2)
        plt.plot([x0-0.05*self.rmax, x0-0.1*self.rmax], [y0, y0], '-r', alpha=0.5, linewidth=2)
        plt.plot([x0+0.05*self.rmax, x0+0.1*self.rmax], [y0, y0], '-r', alpha=0.5, linewidth=2)
        plt.text(0.9*x0, 0.9*y0, r'n$\sigma$=%3.1f'%s0, color='r')
        return
    def _cb_fitFunc(self, r):
        """
        callback function for fitMap
        """
        try:
            if '_k' in r.keys():
                self.allFits[r['_k']] = r
                f = np.sum([0 if a=={} else 1 for a in self.allFits])/float(self.Nfits)
                if f>self._prog:
                    n = int(50*f)
                    print '\033[F',
                    print '|'+'='*(n+1)+' '*(50-n)+'|',
                    print '%2d%%'%(int(100*f)),
                    self._progTime[1] = time.time()
                    print '%3d s remaining'%(int((self._progTime[1]-self._progTime[0])/f*(1-f)))
                    self._prog = max(self._prog+0.01, f+0.01)
            else:
                print '!!! r should have key "_k"'
        except:
            print '!!! I expect a dict!'
        return
    def fitMap(self, step,  fig=1, addfits=False, addCompanion=None, removeCompanion=None):
        """
        - filename: a standard OIFITS data file
        - observables = ['cp', 'v2', 't3'] list of observables to take into account in the file. Default is all
        - N: starts fits on a NxN grid
        - rmax: size of the grid in mas (20 will search between -20mas to +20mas)
        - rmin: do not look into the inner radius, in mas
        - diam: angular diameter of the central star, in mas. If 'v2' are present and in
            "observables", the UD diam will actually be fitted
        - fig=0: the figure number (default 0)
        - addfits: add the fit and fimap in the FITS file, as a binary table
        """
        try:
            N = int(np.ceil(2*self.rmax/step))
        except:
            print 'ERROR: you should define rmax first!'
        fr = 0.02
        print ' > observables:', self.observables

        if not addCompanion is None:
            tmp = {k:addCompanion[k] for k in addCompanion.keys()}
            tmp['f'] = np.abs(tmp['f'])
            self._chi2Data = _injectCompanionData(self._rawData, self._delta, tmp)
            if 'diam*' in addCompanion.keys():
                self.diam = addCompanion['diam*']
        elif not removeCompanion is None:
            tmp = {k:removeCompanion[k] for k in removeCompanion.keys()}
            tmp['f'] = -np.abs(tmp['f'])
            self._chi2Data = _injectCompanionData(self._rawData, self._delta, tmp)
            if 'diam*' in removeCompanion.keys():
                self.diam = removeCompanion['diam*']
        else:
            self._chi2Data = self._rawData

        if self._dataheader!={}:
            print 'found injected companion at', self._dataheader

        print ' > Preliminary analysis'
        self.fitUD()

        X = np.linspace(-self.rmax, self.rmax, N)
        Y = np.linspace(-self.rmax, self.rmax, N)
        self.allFits, self._prog = [{} for k in range(N*N)], 0.0
        self._progTime = [time.time(), time.time()]
        self.Nfits = np.sum((X[:,None]**2+Y[None,:]**2>=self.rmin**2)*
                      (X[:,None]**2+Y[None,:]**2<=self.rmax**2))

        print ' > Grid Fitting %dx%d:'%(N, N),
        # -- estimate how long it will take, in two passes
        params, Ntest = [], max(multiprocessing.cpu_count()-1,1)
        for i in range(Ntest):
            o = np.random.rand()*2*np.pi
            tmp = {'x':np.cos(o)*(self.rmax+self.rmin),
                      'y':np.sin(o)*(self.rmax+self.rmin),
                      'f':0.02, 'diam*':self.diam}
            for _k in self.dwavel.keys():
                tmp['dwavel;'+_k] = self.dwavel[_k]
            params.append((tmp, self._chi2Data, self.observables))
        est = self.estimateRunTime(_fitFunc, params)
        est *= self.Nfits
        print '... it should take about %d seconds'%(int(est))
        if not CONFIG['longExecWarning'] is None and\
             est>CONFIG['longExecWarning']:
            print " > WARNING: this will take too long. "
            print " | Increase CONFIG['longExecWarning'] if you want to run longer computations."
            print " | set it to 'None' and the warning will disapear... at your own risks!"
            return
        print ''
        # -- parallel on N-1 cores
        p = self._pool()
        k = 0
        t0 = time.time()
        params = []
        for y in Y:
            for x in X:
                if x**2+y**2>=self.rmin**2 and x**2+y**2<=self.rmax**2:
                    tmp={'diam*': self.diam, 'f':fr,
                                  'x':x, 'y':y, '_k':k}
                    for _k in self.dwavel.keys():
                        tmp['dwavel;'+_k] = self.dwavel[_k]
                    params.append(tmp)
                    # -- multiple threads:
                    p.apply_async(_fitFunc, (params[-1], self._chi2Data, self.observables),
                                  callback=self._cb_fitFunc)
                    # -- single thread:
                    #self._cb_fitFunc(_fitFunc((params[-1], self._chi2Data, observables)))
                    k += 1
        p.close()
        p.join()
        #print 'it actually took %4.1fs'%(time.time()-t0)

        print ' | Computing map of interpolated Chi2 minima'
        self.allFits = self.allFits[:k-1]

        for i, f in enumerate(self.allFits):
            #f['best']['f'] = np.abs(f['best']['f'])
            f['best']['diam*'] = np.abs(f['best']['diam*'])
            # -- distance from start to finish of the fit
            f['dist'] = np.sqrt((params[i]['x']-f['best']['x'])**2+
                                (params[i]['y']-f['best']['y'])**2)

        # -- count number of unique minima, start with first one, add N sigma:
        allMin = [self.allFits[0]]
        allMin[-1]['nsigma'] =  _nSigmas(self.chi2_UD,  allMin[-1]['chi2'], self.ndata()-1)
        for f in self.allFits:
            chi2 = []
            for a in allMin:
                tmp, n = 0., 0.
                for k in ['x', 'y', 'f', 'diam*']:
                    if f['uncer'][k]!=0 and a['uncer'][k]!=0:
                        tmp += (f['best'][k]-a['best'][k])**2/(f['uncer'][k]**2+a['uncer'][k]**2)
                        n += 1.
                tmp /= n
                chi2.append(tmp)
            if not any([c<=1 for c in chi2]):
                allMin.append(f)
                allMin[-1]['nsigma'] =  _nSigmas(self.chi2_UD,  allMin[-1]['chi2'], self.ndata()-1)

        # -- plot histogram of distance from start to end of fit:
        if False:
            plt.close(10+fig)
            plt.figure(10+fig)
            plt.hist([f['dist'] for f in self.allFits], bins=20, normed=1, label='all fits')
            plt.xlabel('distance start -> best fit (mas)')
            plt.vlines(2*self.rmax/float(N), 0, plt.ylim()[1], color='r', linewidth=2, label='grid size')
            plt.vlines(2*self.rmax/float(N)*np.sqrt(2)/2, 0, plt.ylim()[1], color='r', linewidth=2,
                       label=r'grid size $\sqrt(2)/2$', linestyle='dashed')
            plt.legend()
            plt.text(plt.xlim()[1]-0.03*(plt.xlim()[1]-plt.xlim()[0]),
                     plt.ylim()[1]-0.03*(plt.ylim()[1]-plt.ylim()[0]),
                     '# of unique minima=%d / grid size=%d'%(len(allMin),  N*N),
                     ha='right', va='top')

        # -- is the grid to wide?
        # -> len(allMin) == number of minima
        # -> distance of fit == number of minima
        print ' | %d individual minima for %d fits'%(len(allMin), len(self.allFits))
        print ' | 10, 50, 90 percentiles for fit displacement: %3.1f, %3.1f, %3.1f mas'%(
                                np.percentile([f['dist'] for f in self.allFits], 10),
                                np.percentile([f['dist'] for f in self.allFits], 50),
                                np.percentile([f['dist'] for f in self.allFits], 90),)

        self.Nopt = max(np.sqrt(2*len(allMin)), self.rmax/np.median([f['dist'] for f in self.allFits])*np.sqrt(2))
        self.Nopt = int(np.ceil(self.Nopt))
        self.stepOptFitMap = 2*self.rmax/self.Nopt
        if len(allMin)/float(N*N)>0.5 or\
             2*self.rmax/float(N)*np.sqrt(2)/2>np.median([f['dist'] for f in self.allFits]):
            print ' | \033[41mWARNING, grid is too wide!!!',
            print '--> try step=%4.2fmas\033[0m'%(2.*self.rmax/float(self.Nopt))
            reliability = 'unreliable'
        elif N>1.2*self.Nopt:
            print ' | \033[43mcurrent grid step (%4.2fmas) is too fine!!!'%(2*self.rmax/N),
            print '--> %4.2fmas should be enough\033[0m'%(2.*self.rmax/float(self.Nopt))
            reliability = 'overkill'
        else:
            print ' | \033[42mGrid has the correct steps of %4.2fmas, optimimum step size found to be %4.2fmas\033[0m'%(step, 2.*self.rmax/float(self.Nopt))
            reliability = 'reliable'

        # == plot chi2 min map:
        _X, _Y = np.meshgrid(np.linspace(X[0]-np.diff(X)[0]/2.,
                                         X[-1]+np.diff(X)[0]/2.,
                                         2*len(X)+1),
                             np.linspace(Y[0]-np.diff(Y)[0]/2.,
                                         Y[-1]+np.diff(Y)[0]/2.,
                                         2*len(Y)+1))

        rbf = scipy.interpolate.Rbf([x['best']['x'] for x in self.allFits
                                        if x['best']['x']**2+x['best']['y']**2>self.rmin**2],
                                    [x['best']['y'] for x in self.allFits
                                        if x['best']['x']**2+x['best']['y']**2>self.rmin**2],
                                    [x['chi2'] for x in self.allFits
                                        if x['best']['x']**2+x['best']['y']**2>self.rmin**2],
                                    function='linear')
        _Z = rbf(_X, _Y)
        _Z[_X**2+_Y**2<self.rmin**2] = self.chi2_UD
        _Z[_X**2+_Y**2>self.rmax**2] = self.chi2_UD

        plt.close(fig)
        if CONFIG['suptitle']:
            plt.figure(fig, figsize=(12,5.5))

            plt.subplots_adjust(left=0.1, right=0.99, bottom=0.1, top=0.78,
                                wspace=0.2, hspace=0.2)
        else:
            plt.figure(fig, figsize=(12,5.))
            plt.subplots_adjust(left=0.1, right=0.99, bottom=0.1, top=0.9,
                                wspace=0.2, hspace=0.2)

        title = "CANDID: companion search"
        title += ' using '+', '.join(self.observables)
        title += '\n'+os.path.basename(self.filename)
        if not removeCompanion is None:
            title += '\ncompanion removed at X=%3.2fmas, Y=%3.2fmas, F=%3.2f%%'%(
                    removeCompanion['x'], removeCompanion['y'], 100*removeCompanion['f'])
        if CONFIG['suptitle']:
            plt.suptitle(title, fontsize=14, fontweight='bold')

        ax1 = plt.subplot(1,2,1)
        plt.title('$\chi^2$ best fit / $\chi^2_{UD}$')
        plt.pcolormesh(_X-0.5*self.rmax/float(N), _Y-0.5*self.rmax/float(N), _Z/self.chi2_UD, cmap=CONFIG['default cmap']+'_r')
        plt.colorbar(format='%0.2f')
        if reliability=='unreliable':
            plt.text(0,0,'!! UNRELIABLE !!', color='r', size=30, alpha=0.5,
                     ha='center', va='center', rotation=45)
            plt.text(self.rmax/3,self.rmax/3,'!! UNRELIABLE !!', color='r', size=30, alpha=0.5,
                     ha='center', va='center', rotation=45)
            plt.text(-self.rmax/3,-self.rmax/3,'!! UNRELIABLE !!', color='r', size=30, alpha=0.5,
                     ha='center', va='center', rotation=45)
        for i, f in enumerate(self.allFits):
            plt.plot([f['best']['x'], params[i]['x']],
                     [f['best']['y'], params[i]['y']], '-y',
                     alpha=0.3, linewidth=2)
        plt.xlabel(r'$\Delta \alpha\, \rightarrow$ E (mas)')
        plt.ylabel(r'$\Delta \delta\, \rightarrow$ N (mas)')
        plt.xlim(self.rmax-0.5*self.rmax/float(N), -self.rmax+0.5*self.rmax/float(N))
        plt.ylim(-self.rmax+0.5*self.rmax/float(N), self.rmax-0.5*self.rmax/float(N))
        ax1.set_aspect('equal', 'datalim')

        # -- http://www.aanda.org/articles/aa/pdf/2011/11/aa17719-11.pdf section 3.2
        n_sigma = _nSigmas(self.chi2_UD, _Z, self.ndata()-1)

        ax2 = plt.subplot(1,2,2, sharex=ax1, sharey=ax1)
        plt.title('n$\sigma$ of detection')
        plt.pcolormesh(_X-0.5*self.rmax/float(N), _Y-0.5*self.rmax/float(N), n_sigma, cmap=CONFIG['default cmap'], vmin=0)
        plt.colorbar(format='%0.2f')

        # if self.rmin>0:
        #     c = plt.Circle((0,0), self.rmin, color='k', alpha=0.33, hatch='x')
        #     ax2.add_patch(c)
        ax2.set_aspect('equal', 'datalim')
        # -- invert X axis

        if reliability=='unreliable':
            plt.text(0,0,'!! UNRELIABLE !!', color='r', size=30, alpha=0.5,
                     ha='center', va='center', rotation=45)
            plt.text(self.rmax/2,self.rmax/2,'!! UNRELIABLE !!', color='r', size=30, alpha=0.5,
                     ha='center', va='center', rotation=45)
            plt.text(-self.rmax/2,-self.rmax/2,'!! UNRELIABLE !!', color='r', size=30, alpha=0.5,
                     ha='center', va='center', rotation=45)

        # --
        nsmax = np.max([a['nsigma'] for a in allMin])
        # -- keep nSigma higher than half the max
        allMin2 = filter(lambda x: x['nsigma']>nsmax/2. and a['best']['x']**2+a['best']['y']**2 > self.rmin**2, allMin)
        # -- keep 5 highest nSigma
        allMin2 = [allMin[i] for i in np.argsort([c['chi2'] for c in allMin])[:5]]
        # -- keep highest nSigma
        allMin2 = [allMin[i] for i in np.argsort([c['chi2'] for c in allMin])[:1]]

        for ii, i in enumerate(np.argsort([x['chi2'] for x in allMin2])):
            print ' > BEST FIT %d: chi2=%5.2f'%(ii, allMin2[i]['chi2'])
            for s in ['x', 'y', 'f', 'diam*']:
                print ' | %5s='%s, '%9.2e +- %6.2e'%(allMin2[i]['best'][s], allMin2[i]['uncer'][s])

            # -- http://www.aanda.org/articles/aa/pdf/2011/11/aa17719-11.pdf section 3.2
            print ' | chi2r_UD=%4.2f, chi2r_BIN=%4.2f, NDOF=%d'%(self.chi2_UD, allMin2[i]['chi2'], self.ndata()-1),
            print '-> n sigma: %5.2f'%allMin2[i]['nsigma']
            x0 = allMin2[i]['best']['x']
            ex0 = allMin2[i]['uncer']['x']
            y0 = allMin2[i]['best']['y']
            ey0 = allMin2[i]['uncer']['y']
            f0 = allMin2[i]['best']['f']
            ef0 = allMin2[i]['uncer']['f']
            s0 = allMin2[i]['nsigma']

            # -- draw cross hair
            for ax in [ax1, ax2]:
                ax.plot([x0, x0], [y0-0.05*self.rmax, y0-0.1*self.rmax], '-r', alpha=0.5, linewidth=2)
                ax.plot([x0, x0], [y0+0.05*self.rmax, y0+0.1*self.rmax], '-r', alpha=0.5, linewidth=2)
                ax.plot([x0-0.05*self.rmax, x0-0.1*self.rmax], [y0, y0], '-r', alpha=0.5, linewidth=2)
                ax.plot([x0+0.05*self.rmax, x0+0.1*self.rmax], [y0, y0], '-r', alpha=0.5, linewidth=2)
            ax2.text(0.9*x0, 0.9*y0, r'%3.1f$\sigma$'%s0, color='r')

        plt.xlabel(r'$\Delta \alpha\, \rightarrow$ E (mas)')
        plt.ylabel(r'$\Delta \delta\, \rightarrow$ N (mas)')
        plt.xlim(self.rmax-self.rmax/float(N), -self.rmax+self.rmax/float(N))
        plt.ylim(-self.rmax+self.rmax/float(N), self.rmax-self.rmax/float(N))
        ax2.set_aspect('equal', 'datalim')

        # -- best fit parameters:
        j = np.argmin([x['chi2'] for x in self.allFits if x['best']['x']**2+x['best']['y']**2>self.rmin**2])
        self.compParam = [x['best'] for x in self.allFits if x['best']['x']**2+x['best']['y']**2>self.rmin**2][j]
        self.compUncer = [x['uncer'] for x in self.allFits if x['best']['x']**2+x['best']['y']**2>self.rmin**2][j]
        self.compParam.pop('_k')
        self.compUncer.pop('_k')

        # -- compare with injected companion, if any
        if 'X' in self._dataheader.keys() and 'Y' in self._dataheader.keys() and 'F' in self._dataheader.keys():
            print 'injected X:', self._dataheader['X'], 'found at %3.1f sigmas'%((x0-self._dataheader['X'])/ex0)
            print 'injected Y:', self._dataheader['Y'], 'found at %3.1f sigmas'%((y0-self._dataheader['Y'])/ey0)
            print 'injected F:', self._dataheader['F'], 'found at %3.1f sigmas'%((f0-self._dataheader['F'])/ef0)
            ax1.plot(self._dataheader['X'], self._dataheader['Y'], 'py', markersize=12, alpha=0.3)

        # -- do an additional fit by fitting also the badwidth smearing
        param = self.compParam
        fitAlso = filter(lambda x: x.startswith('dwavel'), param)
        print ' > fixed bandwidth parameters for the map (in um):'
        for s in fitAlso:
            print ' | %s = '%s, param[s]
        print '*'*67
        print '*'*3, 'do an additional fit by fitting also the bandwidth smearing', '*'*3
        print '*'*67

        fit = _fitFunc(param, self._chi2Data, self.observables, fitAlso)
        print '  > chi2 = %5.3f'%fit['chi2']
        tmp = ['x', 'y', 'f', 'diam*']
        tmp.extend(fitAlso)
        for s in tmp:
            print ' | %5s='%s, '%9.2e +- %6.2e'%(fit['best'][s], fit['uncer'][s])

        if addfits:
            #print ' > updating OIFITS file:', filename
            # === add result to fits file:
            #f = fits.open(filename, mode='update')
            #print len(f), f.info()
            #f.close()
            # make new HDU, a binary table will all the fits
            cols = []
            cols.append(fits.Column(name='CHI2', format='E',
                        array=[x['chi2'] for x in self.allFits]))
            cols.append(fits.Column(name='X', format='E',
                        array=[x['best']['x'] for x in self.allFits], unit='mas'))
            cols.append(fits.Column(name='E_X', format='E',
                        array=[x['uncer']['x'] for x in self.allFits], unit='mas'))
            cols.append(fits.Column(name='Y', format='E',
                        array=[x['best']['y'] for x in self.allFits], unit='mas'))
            cols.append(fits.Column(name='E_Y', format='E',
                        array=[x['uncer']['y'] for x in self.allFits], unit='mas'))
            cols.append(fits.Column(name='F', format='E',
                        array=[x['best']['f'] for x in self.allFits]))
            cols.append(fits.Column(name='E_F', format='E',
                        array=[x['uncer']['f'] for x in self.allFits]))
            cols.append(fits.Column(name='DIAM', format='E',
                        array=[x['best']['diam*'] for x in self.allFits], unit='mas'))
            cols.append(fits.Column(name='E_DIAM', format='E',
                        array=[x['uncer']['diam*'] for x in self.allFits], unit='mas'))
            hducols = fits.ColDefs(cols)
            hdu = fits.new_table(hducols)
            hdu.header['EXTNAME'] = 'CANDID_GRIDFIT'
            hdu.header['DATE'] = time.asctime()
            hdu.header['HIERARCH INPUT N'] = N
            hdu.header['HIERARCH INPUT RMIN'] = (self.rmin, 'companion minimum distance, in mas')
            hdu.header['HIERARCH INPUT RMAX'] = (self.rmax, 'max size of the grid, +- mas')
            hdu.header['HIERARCH INPUT OBSERVABLES'] = ', '.join(self.observables)
            # -- fitted parameters:
            hdu.header['HIERARCH BEST DIAM'] = (self.diam, 'UD ang. diameter of star, mas')
            hdu.header['HIERARCH BEST EDIAM'] = (self.ediam, 'uncertainty, mas')
            hdu.header['HIERARCH BEST X'] = (x0, 'in mas')
            hdu.header['HIERARCH BEST EX'] = (ex0, 'uncertainty, in mas')
            hdu.header['HIERARCH BEST Y'] = (y0, 'in mas')
            hdu.header['HIERARCH BEST EY'] = (ey0, 'uncertainty, in mas')
            hdu.header['HIERARCH BEST F'] = (f0, 'flux ratio')
            hdu.header['HIERARCH BEST EF'] = (ef0, 'uncertainty')
            hdu.header['HIERARCH BEST CHI2R BIN'] = (c0, 'reduced chi2 for best binary fit')
            hdu.header['HIERARCH BEST CHI2R UD'] = (self.chi2_UD, 'reduced chi2 for best UD fit')
            hdu.header['HIERARCH BEST NDOF'] = (self.ndata()-1, 'number of degree of freedom')
            hdu.header['HIERARCH BEST NSIGMA'] = (nsigma0, 'number of sigma of the detection')
            hdu.header['HIERARCH QUALITY GRID'] = (reliability, '')
            hdu.header['HIERARCH QUALITY ACTUAL STEP'] = (2*self.rmax/float(N), 'actual grid search step, in mas')
            hdu.header['HIERARCH QUALITY OPTIMUM STEP'] = (2*self.rmax/float(self.Nopt), 'optimum grid search step, in mas')
            # -- remove extension if already present in the OIDITS file:
            for i in range(len(f)-1):
                if self._fitsHandler[i+1].header['EXTNAME'] == hdu.header['EXTNAME']:
                    self._fitsHandler.pop(i+1)
            self._fitsHandler.append(hdu)
        return
    def _cb_nsigmaFunc(self, r):
        """
        callback function for detectionLimit()
        """
        try:
            self.f3s[r[1], r[0]] = r[2]
            # -- completed / to be computed
            f = np.sum(self.f3s>0)/float(np.sum(self.f3s>=0))
            if f>self._prog:
                n = int(50*f)
                print '\033[F',
                print '|'+'='*(n+1)+' '*(50-n)+'|',
                print '%2d%%'%(int(100*f)),
                self._progTime[1] = time.time()
                print '%3d s remaining'%(int((self._progTime[1]-self._progTime[0])/f*(1-f)))
                self._prog = max(self._prog+0.01, f+0.01)
        except:
            print 'did not work'
        return
    def detectionLimit(self, step, diam=None, fig=2, addCompanion=None, removeCompanion=None, drawMaps=True):
        """
        step: number of steps N in the map (map will be NxN)
        drawMaps: display the detection maps in addition to the radial profile (default)

        Apart from the plots, the radial detection limits are stored in the dictionnary
        'self.f3s'.
        """
        try:
            N = int(np.ceil(2*self.rmax/step))
        except:
            print 'ERROR: you should define rmax first!'
            return
        print ' > observables:', self.observables
        if not addCompanion is None:
            tmp = {k:addCompanion[k] for k in addCompanion.keys()}
            tmp['f'] = np.abs(tmp['f'])
            self._chi2Data = _injectCompanionData(self._rawData, self._delta, tmp)
        elif not removeCompanion is None:
            tmp = {k:removeCompanion[k] for k in removeCompanion.keys()}
            tmp['f'] = -np.abs(tmp['f'])
            self._chi2Data = _injectCompanionData(self._rawData, self._delta, tmp)
        else:
            self._chi2Data = self._rawData
        self.fitUD(diam)

        print ' > Detection Limit Map %dx%d'%(N,N),

        # -- estimate how long it will take
        Ntest = max(multiprocessing.cpu_count()-1,1)
        params = []
        for k in range (Ntest):
            tmp = {'x':10*np.random.rand(),
                           'y':10*np.random.rand(),
                           'f':0.01, 'diam*':self.diam}
            for _k in self.dwavel.keys():
                tmp['dwavel;'+_k] = self.dwavel[_k]
            params.append(tmp)
        # -- Absil is twice as fast as injection
        est = 1.5*self.estimateRunTime(_detectLimit, params)
        est *= N**2
        print '... it should take about %d seconds'%(int(est))

        if not CONFIG['longExecWarning'] is None and\
             est>CONFIG['longExecWarning']:
            print " > WARNING: this will take too long. "
            print " | Increase CONFIG['longExecWarning'] if you want to run longer computations."
            print " | set it to 'None' and the warning will disapear... at your own risks!"
            return

        #print 'estimated time:', est
        t0 = time.time()
        # -- prepare grid:
        allX = np.linspace(-self.rmax, self.rmax, N)
        allY = np.linspace(-self.rmax, self.rmax, N)
        self.allf3s = {}
        for method in ['Absil', 'injection']:
            print " > Method:", method
            print ''
            self.f3s = np.zeros((N,N))
            self.f3s[allX[None,:]**2+allY[:,None]**2 >= self.rmax**2] = -1
            self.f3s[allX[None,:]**2+allY[:,None]**2 <= self.rmin**2] = -1
            self._prog = 0.0
            self._progTime = [time.time(), time.time()]
            # -- parallel treatment:
            p = self._pool()
            for i,x in enumerate(allX):
                for j,y in enumerate(allY):
                    if self.f3s[j,i]==0:
                        params = {'x':x, 'y':y, 'f':0.01, 'diam*':self.diam,
                                  '_i':i, '_j':j}
                        for _k in self.dwavel.keys():
                            params['dwavel;'+_k] = self.dwavel[_k]
                        # -- parallel:
                        p.apply_async(_detectLimit, (params, self._chi2Data, self.observables,
                                       self._delta, method), callback=self._cb_nsigmaFunc)
                        # -- single thread:
                        # self._cb_nsigmaFunc(_detectLimit(params, self._chi2Data, self.observables,
                        #                 self._delta, method))
            p.close(); p.join()
            # -- take care of unfitted zone, for esthetics
            self.f3s[self.f3s<=0] = np.median(self.f3s[self.f3s>0])
            self.allf3s[method] = self.f3s.copy()
        #print 'it actually took %4.1f seconds'%(time.time()-t0)
        X, Y = np.meshgrid(allX, allY)
        vmin=min(np.min(100*self.allf3s['Absil']),
                            np.min(100*self.allf3s['injection']))
        vmax=max(np.max(100*self.allf3s['Absil']),
                            np.max(100*self.allf3s['injection']))
        if drawMaps:
            # -- draw the detection maps
            vmin, vmax = None, None
            plt.close(fig)
            if CONFIG['suptitle']:
                plt.figure(fig, figsize=(11,10))
                plt.subplots_adjust(top=0.85, bottom=0.08,
                                    left=0.08, right=0.97)
                title = "CANDID: flux ratio for 3$\sigma$ detection, "
                if self.ediam>0:
                    title += r'fitted $\theta_\mathrm{UD}=%4.3f$ mas.'%(self.diam)
                else:
                    title += r'fixed $\theta_\mathrm{UD}=%4.3f$ mas.'%(self.diam)
                title += ' Using '+', '.join(self.observables)
                title += '\n'+os.path.basename(self.filename)
                if not removeCompanion is None:
                    title += '\ncompanion removed at X=%3.2fmas, Y=%3.2fmas, F=%3.2f%%'%(
                            removeCompanion['x'], removeCompanion['y'], 100*removeCompanion['f'])

                plt.suptitle(title, fontsize=14, fontweight='bold')
            else:
                plt.figure(fig, figsize=(11,9))
                plt.subplots_adjust(top=0.95, bottom=0.08,
                                    left=0.08, right=0.97)
            ax1=plt.subplot(221)
            plt.title("Absil's Method")
            plt.pcolormesh(X, Y, 100*self.allf3s['Absil'], cmap=CONFIG['default cmap'],
                       vmin=vmin, vmax=vmax)
            plt.colorbar()
            plt.xlabel(r'$\Delta \alpha\, \rightarrow$ E (mas)')
            plt.ylabel(r'$\Delta \delta\, \rightarrow$ N (mas)')
            plt.xlim(self.rmax, -self.rmax)
            plt.ylim(-self.rmax, self.rmax)
            ax1.set_aspect('equal', 'datalim')

            ax2=plt.subplot(222)
            plt.title('Companion Injection Method')
            plt.pcolor(X, Y, 100*self.allf3s['injection'], cmap=CONFIG['default cmap'],
                       vmin=vmin, vmax=vmax)
            plt.colorbar()
            plt.xlabel(r'$\Delta \alpha\, \rightarrow$ E (mas)')
            plt.ylabel(r'$\Delta \delta\, \rightarrow$ N (mas)')
            plt.xlim(self.rmax, -self.rmax)
            plt.ylim(-self.rmax, self.rmax)
            ax2.set_aspect('equal', 'datalim')
            plt.subplot(212)

        else:
            plt.close(fig)
            plt.figure(fig, figsize=(8,5))

        # -- radial profile:
        r = np.sqrt(X**2+Y**2).flatten()
        r_f3s = {}
        for k in self.allf3s.keys():
            r_f3s[k] = self.allf3s[k].flatten()[np.argsort(r)]
        r = r[np.argsort(r)]
        for k in r_f3s.keys():
            r_f3s[k] = r_f3s[k][(r<self.rmax)*(r>self.rmin)]
        r = r[(r<self.rmax)*(r>self.rmin)]
        plt.plot(r, 100*sliding_percentile(r, r_f3s['Absil'],
                  self.rmax/float(N), 90),
                '-r', linewidth=3, alpha=0.5, label='Absil 90%')
        plt.plot(r, 100*sliding_percentile(r, r_f3s['injection'],
                  self.rmax/float(N), 90),
                '-b', linewidth=3, alpha=0.5, label='injection 90%')
        plt.legend()
        plt.xlabel('radial distance (mas)')
        plt.ylabel('f$_{3\sigma}$ (%)')
        plt.grid()
        # -- store radial profile of detection limit:
        self.f3s = {'r(mas)':r,
                    'Absil':100*sliding_percentile(r, r_f3s['Absil'],
                                                   self.rmax/float(N), 90),
                    'Injection':100*sliding_percentile(r, r_f3s['injection'],
                                                     self.rmax/float(N), 90),
                  }
        return

def sliding_percentile(x, y, dx, percentile=50, smooth=True):
    res = np.zeros(len(y))
    for i in range(len(x)):
        res[i] = np.percentile(y[np.abs(x-x[i])<dx/2.], percentile)
    if smooth:
        resp = np.zeros(len(y))
        for i in range(len(x)):
            resp[i] = res[np.abs(x-x[i])<dx/4.].mean()
        return resp
    else:
        return res

def _dpfit_leastsqFit(func, x, params, y, err=None, fitOnly=None, verbose=False, doNotFit=[], epsfcn=1e-6, ftol=1e-5, fullOutput=True, normalizedUncer=True, follow=None):
    """
    - params is a Dict containing the first guess.

    - fits 'y +- err = func(x,params)'. errors are optionnal. in case err is a
      ndarray of 2 dimensions, it is treated as the covariance of the
      errors.

      np.array([[err1**2, 0, .., 0],
                [0, err2**2, 0, .., 0],
                [0, .., 0, errN**2]]) is the equivalent of 1D errors

    - follow=[...] list of parameters to "follow" in the fit, i.e. to print in
      verbose mode

    - fitOnly is a LIST of keywords to fit. By default, it fits all
      parameters in 'params'. Alternatively, one can give a list of
      parameters not to be fitted, as 'doNotFit='

    - doNotFit has a similar purpose: for example if params={'a0':,
      'a1': 'b1':, 'b2':}, doNotFit=['a'] will result in fitting only
      'b1' and 'b2'. WARNING: if you name parameter 'A' and another one 'AA',
      you cannot use doNotFit to exclude only 'A' since 'AA' will be excluded as
      well...

    - normalizedUncer=True: the uncertainties are independent of the Chi2, in
      other words the uncertainties are scaled to the Chi2. If set to False, it
      will trust the values of the error bars: it means that if you grossely
      underestimate the data's error bars, the uncertainties of the parameters
      will also be underestimated (and vice versa).

    returns dictionary with:
    'best': bestparam,
    'uncer': uncertainties,
    'chi2': chi2_reduced,
    'model': func(x, bestparam)
    'cov': covariance matrix (normalized if normalizedUncer)
    'fitOnly': names of the columns of 'cov'
    """
    # -- fit all parameters by default
    if fitOnly is None:
        if len(doNotFit)>0:
            fitOnly = filter(lambda x: x not in doNotFit, params.keys())
        else:
            fitOnly = params.keys()
        fitOnly.sort() # makes some display nicer

    # -- build fitted parameters vector:
    pfit = [params[k] for k in fitOnly]

    # -- built fixed parameters dict:
    pfix = {}
    for k in params.keys():
        if k not in fitOnly:
            pfix[k]=params[k]
    if verbose:
        print '[dpfit] %d FITTED parameters:'%(len(fitOnly)), fitOnly
    # -- actual fit
    plsq, cov, info, mesg, ier = \
              scipy.optimize.leastsq(_dpfit_fitFunc, pfit,
                    args=(fitOnly,x,y,err,func,pfix,verbose,follow,),
                    full_output=True, epsfcn=epsfcn, ftol=ftol)
    if isinstance(err, np.ndarray) and len(err.shape)==2:
        print cov

    # -- best fit -> agregate to pfix
    for i,k in enumerate(fitOnly):
        pfix[k] = plsq[i]

    # -- reduced chi2
    model = func(x,pfix)
    tmp = _dpfit_fitFunc(plsq, fitOnly, x, y, err, func, pfix)
    try:
        chi2 = (np.array(tmp)**2).sum()
    except:
        chi2=0.0
        for x in tmp:
            chi2+=np.sum(x**2)
    reducedChi2 = chi2/float(np.sum([1 if np.isscalar(i) else
                                     len(i) for i in tmp])-len(pfit)+1)
    if not np.isscalar(reducedChi2):
        reducedChi2 = np.mean(reducedChi2)

    # -- uncertainties:
    uncer = {}
    for k in pfix.keys():
        if not k in fitOnly:
            uncer[k]=0 # not fitted, uncertatinties to 0
        else:
            i = fitOnly.index(k)
            if cov is None:
                uncer[k]=-1
            else:
                uncer[k]= np.sqrt(np.abs(np.diag(cov)[i]))
                if normalizedUncer:
                    uncer[k] *= np.sqrt(reducedChi2)

    if verbose:
        print '-'*30
        print 'REDUCED CHI2=', reducedChi2
        print '-'*30
        if normalizedUncer:
            print '(uncertainty normalized to data dispersion)'
        else:
            print '(uncertainty assuming error bars are correct)'
        tmp = pfix.keys(); tmp.sort()
        maxLength = np.max(np.array([len(k) for k in tmp]))
        format_ = "'%s':"
        # -- write each parameter and its best fit, as well as error
        # -- writes directly a dictionnary
        print '' # leave some space to the eye
        for ik,k in enumerate(tmp):
            padding = ' '*(maxLength-len(k))
            formatS = format_+padding
            if ik==0:
                formatS = '{'+formatS
            if uncer[k]>0:
                ndigit = -int(np.log10(uncer[k]))+3
                print formatS%k , round(pfix[k], ndigit), ',',
                print '# +/-', round(uncer[k], ndigit)
            elif uncer[k]==0:
                if isinstance(pfix[k], str):
                    print formatS%k , "'"+pfix[k]+"'", ','
                else:
                    print formatS%k , pfix[k], ','
            else:
                print formatS%k , pfix[k], ',',
                print '# +/-', uncer[k]
        print '}' # end of the dictionnary
        try:
            if verbose>1:
                print '-'*3, 'correlations:', '-'*15
                N = np.max([len(k) for k in fitOnly])
                N = min(N,20)
                N = max(N,5)
                sf = '%'+str(N)+'s'
                print ' '*N,
                for k2 in fitOnly:
                    print sf%k2,
                print ''
                sf = '%-'+str(N)+'s'
                for k1 in fitOnly:
                    i1 = fitOnly.index(k1)
                    print sf%k1 ,
                    for k2 in fitOnly:
                        i2 = fitOnly.index(k2)
                        if k1!=k2:
                            print ('%'+str(N)+'.2f')%(cov[i1,i2]/
                                                      np.sqrt(cov[i1,i1]*cov[i2,i2])),
                        else:
                            print ' '*(N-4)+'-'*4,
                    print ''
                print '-'*30
        except:
            pass
    # -- result:
    if fullOutput:
        if normalizedUncer:
            try:
                cov *= reducedChi2
            except:
                pass
        try:
            cor = np.sqrt(np.diag(cov))
            cor = cor[:,None]*cor[None,:]
            cor = cov/cor
        except:
            cor = None

        pfix={'best':pfix, 'uncer':uncer,
              'chi2':reducedChi2, 'model':model,
              'cov':cov, 'fitOnly':fitOnly,
              'info':info, 'cor':cor}
    return pfix

def _dpfit_fitFunc(pfit, pfitKeys, x, y, err=None, func=None, pfix=None, verbose=False, follow=None):
    """
    interface to leastsq from scipy:
    - x,y,err are the data to fit: f(x) = y +- err
    - pfit is a list of the paramters
    - pfitsKeys are the keys to build the dict
    pfit and pfix (optional) and combines the two
    in 'A', in order to call F(X,A)

    in case err is a ndarray of 2 dimensions, it is treated as the
    covariance of the errors.
    np.array([[err1**2, 0, .., 0],
             [ 0, err2**2, 0, .., 0],
             [0, .., 0, errN**2]]) is the equivalent of 1D errors

    """
    global verboseTime
    params = {}
    # -- build dic from parameters to fit and their values:
    for i,k in enumerate(pfitKeys):
        params[k]=pfit[i]
    # -- complete with the non fitted parameters:
    for k in pfix:
        params[k]=pfix[k]
    if err is None:
        err = np.ones(np.array(y).shape)

    # -- compute residuals
    if type(y)==np.ndarray and type(err)==np.ndarray:
        # -- assumes y and err are a numpy array
        y = np.array(y)
        res = ((func(x,params)-y)/err).flatten()
        # -- avoid NaN! -> make them 0's
        res = np.nan_to_num(res)
    else:
        # much slower: this time assumes y (and the result from func) is
        # a list of things, each convertible in np.array
        res = []
        tmp = func(x,params)
        for k in range(len(y)):
            df = (np.array(tmp[k])-np.array(y[k]))/np.array(err[k])
            # -- avoid NaN! -> make them 0's
            df = np.nan_to_num(df)
            try:
                res.extend(list(df))
            except:
                res.append(df)

    if verbose and time.time()>(verboseTime+1):
        verboseTime = time.time()
        print time.asctime(),
        try:
            chi2=(res**2).sum/(len(res)-len(pfit)+1.0)
            print 'CHI2: %6.4e'%chi2,
        except:
            # list of elements
            chi2 = 0
            N = 0
            res2 = []
            for r in res:
                if np.isscalar(r):
                    chi2 += r**2
                    N+=1
                    res2.append(r)
                else:
                    chi2 += np.sum(np.array(r)**2)
                    N+=len(r)
                    res2.extend(list(r))

            res = res2
            print 'CHI2: %6.4e'%(chi2/float(N-len(pfit)+1)),
        if follow is None:
            print ''
        else:
            try:
                print ' '.join([k+'='+'%5.2e'%params[k] for k in follow])
            except:
                print ''
    return res
