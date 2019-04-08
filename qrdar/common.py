import os
import pandas as pd
import numpy as np
import itertools

from sklearn.cluster import DBSCAN
from scipy.spatial import distance_matrix as distance_matrix
from scipy.optimize import curve_fit

from qrdar.io.pcd_io import *

def nn(arr):

    nbrs = NearestNeighbors(n_neighbors=3, algorithm='kd_tree').fit(arr)
    distances, indices = nbrs.kneighbors(arr)
    
    return np.unique(distances[:, 1])

def apply_rotation(M, df):
    
    if 'a' not in df.columns:
        df.loc[:, 'a'] = 1
    
    r_ = np.dot(M, df[['x', 'y', 'z', 'a']].T).T
    df.loc[:, ['x', 'y', 'z']] = r_[:, :3]
    
    return df[['x', 'y', 'z']]

def rigid_transform_3D(A, B):
    
    """
    http://nghiaho.com/uploads/code/rigid_transform_3D.py_
    """
    
    assert len(A) == len(B)
    
    A = np.matrixlib.defmatrix.matrix(A)
    B = np.matrixlib.defmatrix.matrix(B)

    N = A.shape[0]; # total points

    centroid_A = np.mean(A, axis=0).reshape(1, 3)
    centroid_B = np.mean(B, axis=0).reshape(1, 3)
    
    # centre the points
    AA = A - np.tile(centroid_A, (N, 1))
    BB = B - np.tile(centroid_B, (N, 1))

    # dot is matrix multiplication for array
    H = np.transpose(AA) * BB

    U, S, Vt = np.linalg.svd(H)

    R = np.dot(Vt.T, U.T)
    
    t = -R*centroid_A.T + centroid_B.T
    
    M, N = np.identity(4), np.identity(4)
    M[:3, :3] = R
    N[:3, 3] = t.reshape(-1, 3)
    
    return np.dot(N, M)

def gauss(x, mu, sigma, A):
    return A*np.exp(-(x-mu)**2/2/sigma**2)

def bimodal(x,mu1,sigma1,A1,mu2,sigma2,A2):
    return gauss(x,mu1,sigma1,A1)+gauss(x,mu2,sigma2,A2)

def calculate_cutoff(data, p):

    bins = np.linspace(np.floor(data.min()), np.ceil(data.max()))
    y, x = np.histogram(data, bins=bins)
    x = (x[1:] + x[:-1]) / 2 # for len(x)==len(y)
    
    expected = (np.percentile(bins, 0 + p), 1, len(data) / 2, np.percentile(bins, 100 - p), 1, len(data) / 2)
    params, cov = curve_fit(bimodal, x, y, expected, maxfev=10000)
    sigma = np.sqrt(np.diag(cov))
    
    return np.mean([params[0], params[3]])

def expected_distances():
    # distances between points
    template = np.array([[ 0.      ,  0, 0.      ],
                         [ 0.182118,  0, 0.0381  ],
                         [ 0.      ,  0, 0.266446],
                         [ 0.131318,  0, 0.266446]])
    
    dist = set()
    for b in itertools.permutations(template, 2):
        dist.add(np.around(np.linalg.norm(b[0] - b[1]), 2))
        
    return np.hstack([0, np.sort(np.array(list(dist)))])

def extract_tile(corners, marker_df, i, tile_centres, prefix):
    
    tile_names = []
    code = pd.DataFrame()
    
    marker_df.loc[i, ['x', 'y', 'z']] = corners[['x', 'y', 'z']].mean()
    
    # codes may overlap tiles so read in all tiles and append
    for ix, cnr in corners.iterrows():
        tile_name = tile_centres.loc[np.where((np.isclose(cnr.y, tile_centres.y, atol=5) & 
                                               np.isclose(cnr.x, tile_centres.x, atol=5)))].tile.values[0]
        if tile_name not in tile_names:
            tile = pcd_io.read_pcd('../rxp2pcd_i/{}_{}.pcd'.format(prefix, tile_name))
            tile = tile.loc[(tile.x.between(corners.x.min() - .1, corners.x.max() + .1)) & 
                            (tile.y.between(corners.y.min() - .1, corners.y.max() + .1)) &
                            (tile.z.between(corners.z.min() - .1, corners.z.max() + .1))]
            code = code.append(tile)
            tile_names.append(tile_name)
            
    code
            
    return code, tile_names

def ensure_square_arr(df, var):
    
    X, Y = np.meshgrid(np.arange(0, 6), np.arange(0, 6))
    x_df = pd.DataFrame(np.vstack([Y.flatten(), X.flatten()]).T, columns=['xx', 'zz'])
        
    b_df = df.groupby(['xx', 'zz'])[var].mean().reset_index()
    img = pd.merge(x_df, b_df, on=['xx', 'zz'], how='left')
    img.loc[np.isnan(img[var]), var] = 0
    img = img[var].values.reshape(6, 6)
    img = img * np.pad(np.ones(np.array(img.shape) - 2), 1, 'constant') # force border to be black
    return img

def method_1(code):
    
    print '    running method 1'
    code.loc[:, 'I_mean'] = code.groupby(['xx', 'zz']).intensity.transform(np.mean)

    C = 0
    for p in np.arange(5, 50, 5):
        #try:
            C = calculate_cutoff(code.I_mean, p)
            if code.I_mean.min() < C < code.I_mean.max():
                break
        #except:
        #    pass

    code.loc[:, 'bw1'] = np.where(code.I_mean < C, 0, 1)
    img_1 = ensure_square_arr(code, 'bw1')
    return img_1
    #ax.imshow(np.rot90(img_1, 1), cmap=plt.cm.Greys_r, interpolation='none')

def method_2(code):
    
    print '    running method 2'
    code.loc[:, 'N'] = code.groupby(['xx', 'zz']).x.transform(size)
    LN = code[(code.intensity < -7) ].groupby(['xx', 'zz']).x.size().reset_index(name='LN')
    code = pd.merge(LN, code, on=['xx', 'zz'], how='outer')
    code.loc[:, 'P'] = code.LN / code.N

    imgs = []
    
    for ax, threshold in zip([ax4, ax5], [.4, .6]):

        code.loc[:, 'bw2'] = code.P.apply(lambda p: 0 if p > threshold else 1)
        img_2 = ensure_square_arr(code, 'bw2')
        imgs.append(img_2)

    return imgs

def extract_voxel(corners, codeN, tile_centres, prefix, sticker_centres=None, R=np.identity(4)):
    
    if np.array_equal(R, np.identity(4)):
        R = np.identity(4)
        R[:3, 3] = -corners[['x', 'y', 'z']].mean()

    voxel = pd.DataFrame()
    tile_names = []
   
    # codes may overlap tiles so read in all tiles and append
    for ix, cnr in corners.iterrows():
        tile_name_ = tile_centres.loc[np.where((np.isclose(cnr.y, tile_centres.y, atol=10) & 
                                                np.isclose(cnr.x, tile_centres.x, atol=10)))].tile.values
        for tile_name in tile_name_:
            if tile_name in tile_names: continue
            print '        processing tile:', tile_name
            tile_names.append(tile_name)
            tile = pcd_io.read_pcd('../downsample_p/{}_{}.downsample.pcd'.format(prefix, tile_name))
            tile = tile.loc[(tile.x.between(corners.x.min() - 3, corners.x.max() + 3)) & 
                            (tile.y.between(corners.y.min() - 3, corners.y.max() + 3)) &
                            (tile.z.between(corners.z.min() - 2, corners.z.max() + 4))]
            if len(tile) == 0: continue
            # apply rotation
            tile[['x', 'y', 'z']] = apply_rotation(R, tile)
            # filter
            tile = tile[(tile.z.between(0, 4)) &
                        (tile.x.between(-1.5, 1.5)) &
                        (tile.y.between(-2, 2))]
            voxel = voxel.append(tile)       
            
    print '    running DBSCAN on voxel'
    dbscan = DBSCAN(eps=.05, min_samples=25).fit(voxel[['x', 'y', 'z']])
    voxel.loc[:, 'labels_'] = dbscan.labels_
    voxel = voxel[voxel.labels_ != -1] 
    voxel[['x', 'y', 'z']] = apply_rotation(np.linalg.inv(R), voxel)
    
    v = voxel.groupby('labels_').agg([min, max, 'count'])
    stem_cluster = []
    inc = 0
    
    while len(stem_cluster) == 0:
        if sticker_centres is None:
            stem_cluster = v[(corners.x.min() >= v['x']['min'] - inc) & 
                             (corners.x.max() <= v['x']['max'] + inc) &
                             (corners.y.min() >= v['y']['min'] - inc) & 
                             (corners.y.max() <= v['y']['max'] + inc)].index
        else: 
            if inc == 0: sticker_centres[['x', 'y', 'z']] = apply_rotation(np.linalg.inv(R), sticker_centres)
            stem_cluster = v[(sticker_centres.x.min() >= v['x']['min'] - inc) & 
                             (sticker_centres.x.max() <= v['x']['max'] + inc) &
                             (sticker_centres.y.min() >= v['y']['min'] - inc) & 
                             (sticker_centres.y.max() <= v['y']['max'] + inc)].index
        inc += .01
    
    print '    saving stem: ../clusters/cluster_{}.pcd'.format(codeN)
    pcd_io.write_pcd(voxel[voxel.labels_.isin(stem_cluster)], '../clusters/cluster_{}.pcd'.format(codeN))    
#     voxel.to_csv('clusters/clusters_{}.txt'.format(codeN), index=False)
#     code[['x', 'y', 'z', 'intensity']].to_csv('clusters/code_{}.txt'.format(codeN), index=False)

    return voxel, sticker_centres