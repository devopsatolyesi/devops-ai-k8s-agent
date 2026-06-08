# Troubleshooting Scenarios

## CrashLoopBackOff

Kanıtlar:

- Restart count yüksek.
- Event içinde back-off mesajı var.
- Loglarda `connection refused`, `timeout`, `permission denied`, `panic` gibi ifadeler olabilir.

Doğrulama:

```bash
kubectl describe pod POD -n NAMESPACE
kubectl logs POD -n NAMESPACE -c CONTAINER --previous --tail=200
```

## ImagePullBackOff

Olası nedenler:

- Image adı veya tag hatalı.
- Private registry auth secret eksik.
- Registry erişilemiyor.

```bash
kubectl describe pod POD -n NAMESPACE
kubectl get pod POD -n NAMESPACE -o jsonpath='{.spec.imagePullSecrets}'
```

## OOMKilled

Olası nedenler:

- Memory limit düşük.
- Application memory leak var.
- Request/limit production trafiğine uygun değil.

```bash
kubectl describe pod POD -n NAMESPACE
kubectl top pod POD -n NAMESPACE --containers
```

## Service Has No Endpoints

Olası nedenler:

- Service selector Pod label ile eşleşmiyor.
- Pod Ready değil.
- TargetPort yanlış.

```bash
kubectl describe service SERVICE -n NAMESPACE
kubectl get endpoints SERVICE -n NAMESPACE -o yaml
kubectl get pods -n NAMESPACE --show-labels
```

## Ingress Bad Backend

Olası nedenler:

- Backend Service adı yanlış.
- Service port yanlış.
- Ingress controller yok veya IngressClass uyumsuz.

