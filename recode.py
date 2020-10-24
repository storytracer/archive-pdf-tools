import sys
import os
import subprocess
from os import remove
from time import time

from PIL import Image
import fitz

from mrc import KDU_EXPAND, create_mrc_components, encode_mrc_images
from pdfrenderer import TessPDFRenderer, hocr_page_iterator, hocr_to_word_data
from scandata import scandata_xml_get_skip_pages


VERSION = '0.0.1'
SOFTWARE = 'Internet Archive PDF recoder'

# TODO:
# - Store arguments passed to this program in PDF metadata (compression
#   settings, etc)


STOP = None
VERBOSE = False
REPORT_EVERY = None

IMAGE_MODE_PASSTHROUGH = 0
IMAGE_MODE_PIXMAP = 1
IMAGE_MODE_MRC = 2


def create_tess_textonly_pdf(in_pdf, hocr_file, save_path, skip_pages=None):
    hocr_iter = hocr_page_iterator(hocr_file)

    render = TessPDFRenderer()
    render.BeginDocumentHandler()

    skipped_pages = 0

    for idx, (hocr_page, (w, h)) in enumerate(hocr_iter):
        if skip_pages is not None and idx in skip_pages:
            if VERBOSE:
                print('Skipping page %d' % idx)
            skipped_pages += 1
            continue

        page = in_pdf[idx - skipped_pages]

        width = page.rect.width
        height = page.rect.height

        scaler = page.rect.width / w
        ppi = 72 / scaler

        word_data = hocr_to_word_data(hocr_page)
        render.AddImageHandler(word_data, width, height, ppi=ppi)
        if STOP is not None and idx >= STOP:
            break
        if REPORT_EVERY is not None and idx % REPORT_EVERY == 0:
            print('Generated %d PDF text pages.' % idx)

    render.EndDocumentHandler()

    fp = open(save_path, 'wb+')
    fp.write(render._data)
    fp.close()


def insert_images(from_pdf, to_pdf, mode, bg_bitrate=None, fg_bitrate=None):
    # TODO: add to docstring *_bitrate only valid with mode==2

    if VERBOSE:
        print('Converting with image mode:', mode)

    for idx, page in enumerate(to_pdf):
        # XXX: TODO: FIXME: MEGAHACK: For some reason the _imgonly PDFs
        # generated by us have all images on all pages according to pymupdf, so
        # hack around that for now.
        img = sorted(from_pdf.getPageImageList(idx))[idx]
        #img = from_pdf.getPageImageList(idx)[0]

        xref = img[0]
        maskxref = img[1]
        if mode == IMAGE_MODE_PASSTHROUGH:
            image = from_pdf.extractImage(xref)
            page.insertImage(page.rect, stream=image["image"], overlay=False)
        elif mode == IMAGE_MODE_PIXMAP:
            pixmap = fitz.Pixmap(from_pdf, xref)
            page.insertImage(page.rect, pixmap=pixmap, overlay=False)
        elif mode == IMAGE_MODE_MRC:
            # TODO: Do not assume JPX/JPEG2000 here, probe for image format
            image = from_pdf.extractImage(xref)
            jpx = image["image"]
            fp = open('/tmp/img.jpx', 'wb+')
            fp.write(jpx)
            fp.close()

            subprocess.check_call([KDU_EXPAND, '-i', '/tmp/img.jpx', '-o',
                '/tmp/in.tiff'], stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL)

            mask, bg, fg = create_mrc_components(Image.open('/tmp/in.tiff'))
            mask_f, bg_f, fg_f = encode_mrc_images(mask, bg, fg,
                    bg_bitrate=bg_bitrate, fg_bitrate=fg_bitrate)

            bg_contents = open(bg_f, 'rb').read()
            page.insertImage(page.rect, stream=bg_contents, mask=None,
                    overlay=False)

            fg_contents = open(fg_f, 'rb').read()
            mask_contents = open(mask_f, 'rb').read()

            page.insertImage(page.rect, stream=fg_contents, mask=mask_contents,
                    overlay=True)

            # Remove leftover files
            remove(mask_f)
            remove(bg_f)
            remove(fg_f)

        if STOP is not None and idx >= STOP:
            break

        if REPORT_EVERY is not None and idx % REPORT_EVERY == 0:
            print('Processed %d PDF pages.' % idx)


# XXX: tmp.icc - pick proper one and ship it with the tool, or embed it
def write_pdfa(to_pdf, iccpath='tmp.icc'):
    srgbxref = to_pdf._getNewXref()
    to_pdf.updateObject(srgbxref, """
<<
      /Alternate /DeviceRGB
      /N 3
>>
""")
    to_pdf.updateStream(srgbxref, open(iccpath, 'rb').read(), new=True)

    intentxref = to_pdf._getNewXref()
    to_pdf.updateObject(intentxref, """
<<
  /Type /OutputIntent
  /S /GTS_PDFA1
  /OutputConditionIdentifier (Custom)
  /Info (sRGB IEC61966-2.1)
  /DestOutputProfile %d 0 R
>>
""" % srgbxref)

    catalogxref = to_pdf.PDFCatalog()
    s = to_pdf.xrefObject(to_pdf.PDFCatalog())
    s = s[:-2]
    s += '  /OutputIntents [ %d 0 R ]' % intentxref
    s += '>>'
    to_pdf.updateObject(catalogxref, s)


def write_metadata(from_pdf, to_pdf):
    # TODO: pass more metadata as args
    doc_md = in_pdf.metadata

    #Metadata: {'format': 'PDF 1.5', 'title': None, 'author': None, 'subject': None, 'keywords': None, 'creator': None, 'producer': None, 'creationDate': None, 'modDate': None, 'encryption': None}

    # TODO: Set other metadata keys (as above):
    # - keywords

    # TODO: Make sure to link back to archive.org item somehow? (optional extra
    # metadata args)

    doc_md['producer'] = '%s (version %s)' % (SOFTWARE, VERSION)
    # XXX: identifier / url here
    doc_md['keywords'] = 'https://archive.org/details/ITEMHERE'

    to_pdf.setMetadata(doc_md)

    # TODO: Update this and make sure it's all nice and correct
    # TODO: Write/add:
    # - CreatorTool
    # - CreateDate
    # - MetadataDate
    # - ModifyDate
    # - Title
    # - Creator
    # - Language bag
    # - *link back* to archive.org item
    stream=b'''<?xpacket begin="..." id="W5M0MpCehiHzreSzNTczkc9d"?>
    <x:xmpmeta xmlns:x="adobe:ns:meta/">
      <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
        <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
          <xmp:CreateDate>2020-10-15T01:06:14+00:00</xmp:CreateDate>
          <xmp:MetadataDate>2020-10-15T01:06:14+00:00</xmp:MetadataDate>
          <xmp:ModifyDate>2020-10-15T01:06:14+00:00</xmp:ModifyDate>
          <xmp:CreatorTool>Internet Archive</xmp:CreatorTool>
        </rdf:Description>
        <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>
            <rdf:Alt>
              <rdf:li xml:lang="x-default">a delightful collection of various items for testing</rdf:li>
            </rdf:Alt>
          </dc:title>
          <dc:creator>
            <rdf:Seq>
              <rdf:li>Example, Joe</rdf:li>
            </rdf:Seq>
          </dc:creator>
          <dc:language>
            <rdf:Bag>
              <rdf:li>en</rdf:li>
            </rdf:Bag>
          </dc:language>
        </rdf:Description>
        <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
          <pdfaid:part>3</pdfaid:part>
          <pdfaid:conformance>B</pdfaid:conformance>
        </rdf:Description>
      </rdf:RDF>
    </x:xmpmeta>
    <?xpacket end="r"?> '''

    to_pdf.setXmlMetadata(stream.decode('utf-8'))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
            description='PDF recoder version %s.' % VERSION +
                        ' Compresses PDFs with images and inserts text layers '
                        ' based on hOCR input files.')
    parser.add_argument('-i', '--from-pdf', type=str, default=None,
                        help='Input PDF (containing images) to recode')
    parser.add_argument('-T', '--hocr-file', type=str, default=None,
                        help='hOCR file containing page information '
                              '(currently not optional)')
    parser.add_argument('-S', '--scandata-file', type=str, default=None,
                        help='Scandata XML file containing information on '
                              'which pages to skip (optional). This is helpful '
                              'if the input PDF is a PDF where certain '
                              'pages have already been skipped, but the hOCR '
                              'still has the pages in its file structure.')
    parser.add_argument('-o', '--out-pdf', type=str, default=None,
                        help='Output file to write recoded PDF to.')
    parser.add_argument('-m', '--image-mode', default=IMAGE_MODE_MRC,
                        help='Compression mode. 0 is pass-through, 1 is pixmap'
                              ' 2 is MRC (default is 2)', type=int)
    parser.add_argument('-v', '--verbose', default=False, action='store_true',
                        help='Verbose output')
    parser.add_argument('--report-every', default=None, type=int,
                        help='Briefly repor on status every N pages '
                             '(default is no reporting)')
    parser.add_argument('-t', '--stop-after', default=None, type=int,
                        help='Stop after N pages (default is no stop)')
    parser.add_argument('--bg-bitrate', default=0.1, type=float,
                        help='Bits per pixels for background layer.'
                             'Default is 0.1')
    parser.add_argument('--fg-bitrate', default=0.05, type=float,
                        help='Bits per pixels for foreground layer.'
                             'Default is 0.05')

    # TODO: Lots of options for various metadata parts to write:
    # --metadata-url (url to document)
    # --metadata-title
    # --metadata-creator
    # --metadata-language
    # --metadata-ETC


    args = parser.parse_args()
    if args.from_pdf is None or args.out_pdf is None:
        sys.stderr.write('***** Error: --from-pdf or --out-pdf missing\n\n')
        parser.print_help()
        sys.exit(1)

    in_pdf = fitz.open(args.from_pdf)
    hocr_file = args.hocr_file
    outfile = args.out_pdf

    VERBOSE = args.verbose
    REPORT_EVERY = args.report_every
    STOP = args.stop_after
    if STOP is not None:
        STOP -= 1

    start_time = time()

    skip_pages = []
    if args.scandata_file is not None:
        skip_pages = scandata_xml_get_skip_pages(args.scandata_file)

    # TODO: read scandata and use it to skip pages

    # TODO: use tempfile for this, or even a buffer, since it's typically quite
    # small
    tess_tmp_path = '/tmp/tess.pdf'

    if args.verbose:
        print('Creating text only PDF')

    # 1. Create text-only PDF from hOCR first, but honour page sizes of in_pdf
    create_tess_textonly_pdf(in_pdf, hocr_file, tess_tmp_path, skip_pages=skip_pages)

    if args.verbose:
        print('Inserting (and compressing) images')
    # 2. Load tesseract PDF and stick images in the PDF
    # We open the generated file but do not modify it in place
    outdoc = fitz.open(tess_tmp_path)
    insert_images(in_pdf, outdoc, mode=args.image_mode,
                  bg_bitrate=args.bg_bitrate, fg_bitrate=args.fg_bitrate)

    # 3. Add PDF/A compliant data
    write_pdfa(outdoc)

    # 4. Write metadata
    write_metadata(in_pdf, outdoc)

    # 5. Save
    if VERBOSE:
        print('mupdf warnings, if any:', repr(fitz.TOOLS.mupdf_warnings()))
    if VERBOSE:
        print('Saving PDF now')
    outdoc.save(outfile, deflate=True, pretty=True)

    end_time = time()
    print('Processed %d pages at %.2f seconds/page' % (len(outdoc),
        (end_time - start_time) / len(outdoc)))

    oldsize = os.path.getsize(args.from_pdf)
    newsize = os.path.getsize(args.out_pdf)
    if VERBOSE:
        print('Compression ratio: %f%%' % (oldsize / newsize))

    # 5. Remove leftover files
    remove(tess_tmp_path)
