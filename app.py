2026-08-01T07:50:14.734817462Z [2026-08-01 07:50:14,733] ERROR in app: Exception on / [GET]
2026-08-01T07:50:14.734843273Z Traceback (most recent call last):
2026-08-01T07:50:14.734848614Z   File "/usr/local/lib/python3.9/dist-packages/flask/app.py", line 1511, in wsgi_app
2026-08-01T07:50:14.734852994Z     response = self.full_dispatch_request()
2026-08-01T07:50:14.734857514Z   File "/usr/local/lib/python3.9/dist-packages/flask/app.py", line 919, in full_dispatch_request
2026-08-01T07:50:14.734861394Z     rv = self.handle_user_exception(e)
2026-08-01T07:50:14.734865735Z   File "/usr/local/lib/python3.9/dist-packages/flask/app.py", line 917, in full_dispatch_request
2026-08-01T07:50:14.734870005Z     rv = self.dispatch_request()
2026-08-01T07:50:14.734873905Z   File "/usr/local/lib/python3.9/dist-packages/flask/app.py", line 902, in dispatch_request
2026-08-01T07:50:14.734878795Z     return self.ensure_sync(self.view_functions[rule.endpoint])(**view_args)  # type: ignore[no-any-return]
2026-08-01T07:50:14.734882916Z   File "/app/app.py", line 170, in render_dashboard
2026-08-01T07:50:14.734887186Z     img_inverted = ImageOps.invert(img_1bit)
2026-08-01T07:50:14.734891726Z   File "/usr/lib/python3/dist-packages/PIL/ImageOps.py", line 489, in invert
2026-08-01T07:50:14.734895707Z     return _lut(image, lut)
2026-08-01T07:50:14.734899917Z   File "/usr/lib/python3/dist-packages/PIL/ImageOps.py", line 57, in _lut
2026-08-01T07:50:14.734904047Z     raise OSError("not supported for this image mode")
2026-08-01T07:50:14.734908557Z OSError: not supported for this image mode
2026-08-01T07:50:30.294823894Z ==> Deploying...
2026-08-01T07:50:30.40797632Z ==> Setting WEB_CONCURRENCY=1 by default, based on available CPUs in the instance
2026-08-01T07:50:36.55450571Z [2026-08-01 07:50:36 +0000] [7] [INFO] Starting gunicorn 23.0.0
2026-08-01T07:50:36.554742487Z [2026-08-01 07:50:36 +0000] [7] [INFO] Listening at: http://0.0.0.0:10000 (7)
2026-08-01T07:50:36.554762617Z [2026-08-01 07:50:36 +0000] [7] [INFO] Using worker: sync
2026-08-01T07:50:36.557156178Z [2026-08-01 07:50:36 +0000] [8] [INFO] Booting worker with pid: 8
2026-08-01T07:50:41.413492015Z ==> Your service is live 🎉
2026-08-01T07:50:41.570384936Z ==> 
2026-08-01T07:50:41.57241862Z ==> ///////////////////////////////////////////////////////////
2026-08-01T07:50:41.57452331Z ==> 
2026-08-01T07:50:41.576298567Z ==> Available at your primary URL https://mealsync-cloud.onrender.com
2026-08-01T07:50:41.577948526Z ==> 
2026-08-01T07:50:41.579455216Z ==> ///////////////////////////////////////////////////////////
2026-08-01T07:50:41.844226571Z [2026-08-01 07:50:41,843] ERROR in app: Exception on / [GET]
2026-08-01T07:50:41.844293055Z Traceback (most recent call last):
2026-08-01T07:50:41.844299586Z   File "/usr/local/lib/python3.9/dist-packages/flask/app.py", line 1511, in wsgi_app
2026-08-01T07:50:41.844396492Z     response = self.full_dispatch_request()
2026-08-01T07:50:41.844400872Z   File "/usr/local/lib/python3.9/dist-packages/flask/app.py", line 919, in full_dispatch_request
2026-08-01T07:50:41.844404022Z     rv = self.handle_user_exception(e)
2026-08-01T07:50:41.844406822Z   File "/usr/local/lib/python3.9/dist-packages/flask/app.py", line 917, in full_dispatch_request
2026-08-01T07:50:41.844410193Z     rv = self.dispatch_request()
2026-08-01T07:50:41.844412853Z   File "/usr/local/lib/python3.9/dist-packages/flask/app.py", line 902, in dispatch_request
2026-08-01T07:50:41.844416633Z     return self.ensure_sync(self.view_functions[rule.endpoint])(**view_args)  # type: ignore[no-any-return]
2026-08-01T07:50:41.844419333Z   File "/app/app.py", line 170, in render_dashboard
2026-08-01T07:50:41.844422293Z     img_inverted = ImageOps.invert(img_1bit)
2026-08-01T07:50:41.844425614Z   File "/usr/lib/python3/dist-packages/PIL/ImageOps.py", line 489, in invert
2026-08-01T07:50:41.844428334Z     return _lut(image, lut)
2026-08-01T07:50:41.844431674Z   File "/usr/lib/python3/dist-packages/PIL/ImageOps.py", line 57, in _lut
2026-08-01T07:50:41.844434934Z     raise OSError("not supported for this image mode")
2026-08-01T07:50:41.844437744Z OSError: not supported for this image mode
