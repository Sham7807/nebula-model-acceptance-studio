/* The server and portable exports read one report stylesheet. The standalone
 * builder replaces this loader with the exact same CSS embedded in the file. */
(function(root){
  'use strict';
  const source=root.document.currentScript?.src;
  root.WORKBENCH_REPORT_THEME_READY=fetch(new URL('report-theme.css',source||root.document.baseURI)).then(response=>{
    if(!response.ok)throw new Error('报告样式加载失败');return response.text();
  }).then(css=>{root.WORKBENCH_REPORT_THEME=css;return css;}).catch(()=>null);
})(window);
