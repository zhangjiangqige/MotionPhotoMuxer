import argparse
import logging
import os
import sys
from os.path import exists, basename, isdir
import shutil
import subprocess

import pyexiv2

def validate_directory(dir):
    if not exists(dir):
        logging.error("Path doesn't exist: {}".format(dir))
        exit(1)
    if not isdir(dir):
        logging.error("Path is not a directory: {}".format(dir))
        exit(1)

def validate_media(photo_path, video_path):
    """
    Checks if the files provided are valid inputs. Currently the only supported inputs are MP4/MOV and JPEG filetypes.
    Currently it only checks file extensions instead of actually checking file formats via file signature bytes.
    :param photo_path: path to the photo file
    :param video_path: path to the video file
    :return: True if photo and video files are valid, else False
    """
    if not exists(photo_path):
        logging.error("Photo does not exist: {}".format(photo_path))
        return False
    if not exists(video_path):
        logging.error("Video does not exist: {}".format(video_path))
        return False
    if not photo_path.lower().endswith(('.jpg', '.jpeg', '.heic')):
        logging.error("Photo isn't a JPEG: {}".format(photo_path))
        return False
    if not video_path.lower().endswith(('.mov', '.mp4')):
        logging.error("Video isn't a MOV or MP4: {}".format(photo_path))
        return False
    return True

def copy_imgs(photo_path, output_path):
    """copies photo to output dir so that we can run exiftool"""
    out_path = os.path.join(output_path, "{}".format(basename(photo_path)))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as outfile, open(photo_path, "rb") as photo:
        outfile.write(photo.read())
    return out_path

def run_exiftool(file, offset):
    args = [
        'exiftool',
        '-overwrite_original',
        '-xmp:MicroVideo=1',
        '-xmp:MicroVideoVersion=1',
        '-xmp:MicroVideoOffset={}'.format(offset),
        # in Apple Live Photos, the chosen photo is 1.5s after the start of the video, so 1500000 microseconds
        '-xmp:MicroVideoPresentationTimestampUs=1500000',
        file
    ]
    logging.info('Running > ' + ' '.join(args))
    completed_process = subprocess.run(args, capture_output=True, text=True)
    return completed_process

def append_vid(merged_path, video_path):
    """append video content to output file after exiftool has added xmp to photo"""
    with open(merged_path, "ab") as outfile, open(video_path, "rb") as video:
        outfile.write(video.read())
    return merged_path


def merge_files(photo_path, video_path, output_path):
    """Merges the photo and video file together by concatenating the video at the end of the photo. Writes the output to
    a temporary folder.
    :param photo_path: Path to the photo
    :param video_path: Path to the video
    :return: File name of the merged output file
    """
    logging.info("Merging {} and {}.".format(photo_path, video_path))
    out_path = os.path.join(output_path, "{}".format(basename(photo_path)))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as outfile, open(photo_path, "rb") as photo, open(video_path, "rb") as video:
        outfile.write(photo.read())
        outfile.write(video.read())
    return out_path


def add_xmp_metadata(merged_file, offset):
    return add_xmp_metadata_exiftool(merged_file, offset)

def add_xmp_metadata_exiftool(merged_file, offset):
    """Adds XMP metadata to the merged image indicating the byte offset in the file where the video begins.
    :param merged_file: The path to the file that has the photo and video merged together.
    :param offset: The number of bytes from EOF to the beginning of the video.
    :return: None
    """
    completed_process = run_exiftool(merged_file, offset)
    if 'looks more like a JPEG' in completed_process.stderr:
        new_file = merged_file.replace('.HEIC', '.JPG')
        new_file = new_file.replace('.heic', '.jpg')
        logging.warning('Renaming {} to {}'.format(merged_file, new_file))
        os.rename(merged_file, new_file)
        completed_process = run_exiftool(new_file, offset)
        logging.info('stderr: {}'.format(completed_process.stderr.strip()))
        logging.info('stdout: {}'.format(completed_process.stdout.strip()))
        return new_file
    logging.info('stderr: {}'.format(completed_process.stderr.strip()))
    logging.info('stdout: {}'.format(completed_process.stdout.strip()))
    return merged_file

def add_xmp_metadata_pyexiv2(merged_file, offset):
    """Adds XMP metadata to the merged image indicating the byte offset in the file where the video begins.
    :param merged_file: The path to the file that has the photo and video merged together.
    :param offset: The number of bytes from EOF to the beginning of the video.
    :return: None
    """
    logging.debug("Adding metadata to file {}.".format(merged_file))
    metadata = pyexiv2.ImageMetadata(merged_file)
    metadata.read()
    if len(metadata.xmp_keys) > 0:
        logging.warning("Found existing XMP keys: " + str(metadata.xmp_keys))

    try:
        pyexiv2.xmp.register_namespace('http://ns.google.com/photos/1.0/camera/', 'GCamera')
    except KeyError:
        pass
        # logging.warning("exiv2 detected that the GCamera namespace already exists.".format(merged_file))
    metadata['Xmp.GCamera.MicroVideo'] = pyexiv2.XmpTag('Xmp.GCamera.MicroVideo', 1)
    metadata['Xmp.GCamera.MicroVideoVersion'] = pyexiv2.XmpTag('Xmp.GCamera.MicroVideoVersion', 1)
    metadata['Xmp.GCamera.MicroVideoOffset'] = pyexiv2.XmpTag('Xmp.GCamera.MicroVideoOffset', offset)
    metadata['Xmp.GCamera.MicroVideoPresentationTimestampUs'] = pyexiv2.XmpTag(
        'Xmp.GCamera.MicroVideoPresentationTimestampUs',
        1500000)  # in Apple Live Photos, the chosen photo is 1.5s after the start of the video, so 1500000 microseconds
    metadata.write()
    # pyexiv2.xmp.unregister_namespace('http://ns.google.com/photos/1.0/camera/')
    logging.warning("New XMP values:")
    for k in metadata.xmp_keys:
        rawval = None
        try:
            rawval = metadata[k].raw_value
        except KeyError as e:
            # print(e)
            pass
        print("-xmp:{}={} \\".format(k.replace('Xmp.GCamera.', ''), rawval))


def convert(photo_path, video_path, output_path):
    """
    Performs the conversion process to mux the files together into a Google Motion Photo.
    :param photo_path: path to the photo to merge
    :param video_path: path to the video to merge
    :return: True if conversion was successful, else False
    """
    # merged = merge_files(photo_path, video_path, output_path)
    merged = copy_imgs(photo_path, output_path)

    # photo_filesize = os.path.getsize(photo_path)
    # merged_filesize = os.path.getsize(merged)

    # The 'offset' field in the XMP metadata should be the offset (in bytes) from the end of the file to the part
    # where the video portion of the merged file begins. In other words, merged size - photo_only_size = offset.
    # offset = merged_filesize - photo_filesize
    offset = os.path.getsize(video_path)

    merged = add_xmp_metadata(merged, offset)
    append_vid(merged, video_path)

def matching_video(photo_path):
    base = os.path.splitext(photo_path)[0]
    logging.info("Looking for videos named: {}".format(base))
    if os.path.exists(base + ".mov"):
        return base + ".mov"
    if os.path.exists(base + ".mp4"):
        return base + ".mp4"
    if os.path.exists(base + ".MOV"):
        return base + ".MOV"
    if os.path.exists(base + ".MP4"):
        return base + ".MP4"
    else:
        return ""


def process_directory(file_dir, recurse, outdir):
    """
    Loops through files in the specified directory and generates a list of (photo, video) path tuples that can
    be converted
    :TODO: Implement recursive scan
    :param file_dir: directory to look for photos/videos to convert
    :param recurse: if true, subdirectories will recursively be processes
    :return: a list of tuples containing matched photo/video pairs.
    """
    logging.info("Processing dir: {}".format(file_dir))
    if recurse:
        logging.error("Recursive traversal is not implemented yet.")
        exit(1)

    file_pairs = []
    leftover_cand = []
    for file in os.listdir(file_dir):
        if '.DS_Store' in file:
            continue
        file_fullpath = os.path.join(file_dir, file)
        if os.path.isfile(file_fullpath) and file.lower().endswith(('.jpg', '.jpeg', '.heic')) and matching_video(
                file_fullpath) != "":
            file_pairs.append((file_fullpath, matching_video(file_fullpath)))
        else:
            leftover_cand.append(file_fullpath)

    allvideos = set([vid.lower() for (img, vid) in file_pairs])
    leftover = []
    for lo in leftover_cand:
        if lo.lower() not in allvideos:
            leftover.append(lo)

    logging.info("Found {} pairs, {} left over.".format(len(file_pairs), len(leftover)))
    logging.info("Left overs:")
    for lo in leftover:
        logging.info(lo)
        shutil.copy2(lo, outdir)
    logging.info("Image/video pairs:")
    for (img, vid) in file_pairs:
        logging.info('{} {}'.format(img, vid))
    return file_pairs


def main(args):
    logging_level = logging.INFO if args.verbose else logging.ERROR
    logging.basicConfig(level=logging_level, stream=sys.stdout)
    logging.info("Enabled verbose logging")

    outdir = args.output if args.output is not None else args.dir + "-output"
    os.makedirs(outdir, exist_ok=True)

    if args.dir is not None:
        validate_directory(args.dir)
        pairs = process_directory(args.dir, args.recurse, outdir)
        for pair in pairs:
            if validate_media(pair[0], pair[1]):
                convert(pair[0], pair[1], outdir)
    else:
        if args.photo is None and args.video is None:
            logging.error("Either --dir or --photo and --video are required.")
            exit(1)

        if bool(args.photo) ^ bool(args.video):
            logging.error("Both --photo and --video must be provided.")
            exit(1)

        if validate_media(args.photo, args.video):
            convert(args.photo, args.video, outdir)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Merges a photo and video into a Microvideo-formatted Google Motion Photo')
    parser.add_argument('--verbose', help='Show logging messages.', action='store_true')
    parser.add_argument('--dir', type=str, help='Process a directory for photos/videos. Takes precedence over '
                                                '--photo/--video')
    parser.add_argument('--recurse', help='Recursively process a directory. Only applies if --dir is also provided',
                        action='store_true')
    parser.add_argument('--photo', type=str, help='Path to the JPEG photo to add.')
    parser.add_argument('--video', type=str, help='Path to the MOV video to add.')
    parser.add_argument('--output', type=str, help='Path to where files should be written out to.')
    main(parser.parse_args())
