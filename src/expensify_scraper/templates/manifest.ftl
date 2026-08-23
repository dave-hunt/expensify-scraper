<#function q value>
  <#if !value??><#return ""></#if>
  <#return '"' + value?string?replace('"','""') + '"'>
</#function>
transactionID,reportID,reportName,created,modifiedCreated,merchant,amount,currency,category,receiptID,receiptURL,receiptFilename,receiptSource,filename,receiptState,receiptObjectUrl
<#list reports as report><#list report.transactionList as expense>
${q(expense.transactionID)},${q(expense.reportID)},${q(report.reportName)},${q(expense.created)},${q(expense.modifiedCreated)},${q(expense.merchant)},${expense.amount?c},${q(expense.currency)},${q(expense.category)},${q(expense.receiptID)},${q(expense.receiptURL)},${q(expense.receiptFilename)},${q(expense.receiptSource)},${q(expense.filename)},<#if expense.receipt??>${q(expense.receipt.state)}<#else>""</#if>,<#if expense.receiptObject?? && expense.receiptObject.url??>${q(expense.receiptObject.url)}<#else>""</#if>
</#list></#list>
